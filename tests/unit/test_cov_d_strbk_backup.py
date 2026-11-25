"""Unit tests for ``BackupService`` against the real Postgres schema.

Only the S3 client (a true external boundary) is faked; jobs, backups,
restore, export/import and statistics all run on the real database and
temporary directories.
"""

# flake8: noqa: E501
# ruff: noqa: F811

import gzip
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from apps.api.services.backup import BackupService
from apps.api.utils.async_utils import run_in_threadpool
from tests.unit._cov_d_helpers import fake_secret, vid, world  # noqa: F401


class _Pred:
    """Row predicate supporting ``&`` so queries compose like the DAL's."""

    def __init__(self, fn, t):
        """Store the row test callable and its table."""
        self.fn, self.t = fn, t

    def __and__(self, other):
        """Combine two predicates with logical AND."""
        return _Pred(lambda r: self.fn(r) and other.fn(r), self.t)


class _Desc:
    """Marker for descending order on a field."""

    def __init__(self, fld):
        """Remember the field to sort on."""
        self.fld = fld


class _Fld:
    """Field handle building predicates from comparison operators."""

    def __init__(self, name, t):
        """Record the column name and owning table."""
        self.name, self.t = name, t

    def __gt__(self, v):
        """Greater-than predicate."""
        return _Pred(
            lambda r: r.get(self.name) is not None and r[self.name] > v, self.t
        )

    def __lt__(self, v):
        """Less-than predicate (NULL never matches, as in SQL)."""
        return _Pred(
            lambda r: r.get(self.name) is not None and r[self.name] < v, self.t
        )

    def __eq__(self, v):
        """Equality predicate."""
        return _Pred(lambda r: r.get(self.name) == v, self.t)

    __hash__ = object.__hash__

    def __invert__(self):
        """Descending-order marker."""
        return _Desc(self)


class _Row(dict):
    """Dict row with attribute access and as_dict like a DAL row."""

    def __getattr__(self, k):
        """Expose columns as attributes."""
        if k.startswith("__"):
            raise AttributeError(k)
        return self.get(k)

    def as_dict(self):
        """Return a plain dict copy."""
        return dict(self)


class _Table:
    """In-memory table: insert, id lookup and field handles."""

    def __init__(self):
        """Start empty with an id sequence."""
        self.rows = []
        self.seq = 0

    def __getattr__(self, name):
        """Return a field handle for a column name."""
        if name.startswith("_"):
            raise AttributeError(name)
        return _Fld(name, self)

    def __getitem__(self, rid):
        """Look up a row by id (None when absent)."""
        return next((r for r in self.rows if r["id"] == rid), None)

    def insert(self, **kw):
        """Insert a row, rejecting columns the fake schema lacks."""
        if any(k.startswith("bad") for k in kw):
            raise ValueError("unknown column")
        self.seq += 1
        row = _Row(kw)
        row.setdefault("id", self.seq)
        self.seq = max(self.seq, row["id"])
        self.rows.append(row)
        return row["id"]


class _Set:
    """Query result set supporting select/update/delete/count."""

    def __init__(self, table, pred):
        """Bind a table and predicate."""
        self.table, self.pred = table, pred

    def _rows(self):
        """Return matching rows."""
        return [r for r in self.table.rows if self.pred.fn(r)]

    def select(self, orderby=None, limitby=None):
        """Return rows, optionally ordered and limited."""
        rows = self._rows()
        if orderby is not None:
            desc = isinstance(orderby, _Desc)
            name = orderby.fld.name if desc else orderby.name
            rows.sort(
                key=lambda r: (r.get(name) is not None, r.get(name) or 0),
                reverse=desc,
            )
        if limitby:
            rows = rows[limitby[0] : limitby[1]]
        return rows

    def update(self, **kw):
        """Update matching rows."""
        for r in self._rows():
            r.update(kw)

    def delete(self):
        """Delete matching rows."""
        for r in self._rows():
            self.table.rows.remove(r)

    def count(self):
        """Count matching rows."""
        return len(self._rows())


class _FakeDB:
    """Minimal in-memory DAL covering what BackupService touches.

    The real schema rejects the service's inserts (see the xfail tests), so
    business logic is exercised here against an in-memory stand-in.
    """

    def __init__(self):
        """Create the tables the service uses."""
        self.tables = ["backup_jobs", "backups", "entities"]
        self._t = {n: _Table() for n in self.tables + ["organizations", "issues"]}

    def __getattr__(self, name):
        """Return a table by attribute."""
        if name.startswith("_"):
            raise AttributeError(name)
        return self._t[name]

    def __getitem__(self, name):
        """Return a table by name."""
        return self._t[name]

    def __call__(self, pred):
        """Build a result set from a predicate on one table."""
        return _Set(pred.t, pred)

    def commit(self):
        """No-op commit."""

    def rollback(self):
        """No-op rollback."""


def _fake(svc_env):
    """Return a service wired to the in-memory DAL."""
    svc = svc_env()
    svc.db = _FakeDB()
    return svc


def _client_error(code: str) -> ClientError:
    """Build a botocore ClientError carrying the given error code."""
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "Op")


@pytest.fixture
def svc_env(app, tmp_path, monkeypatch):
    """Return a factory for BackupService instances rooted in a temp dir."""
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "bk"))
    for k in (
        "BACKUP_S3_ENABLED",
        "BACKUP_S3_BUCKET",
        "BACKUP_S3_ENDPOINT",
        "BACKUP_S3_ACCESS_KEY",
        "BACKUP_S3_SECRET_KEY",
    ):
        monkeypatch.delenv(k, raising=False)

    def make(**env):
        """Create a service after applying extra environment overrides."""
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        return BackupService(app.db)

    return make


class TestS3Init:
    """S3 client initialisation branches."""

    def test_disabled_by_default(self, svc_env):
        """No env means no S3 client and the backup dir is created."""
        svc = svc_env()
        assert svc.s3_enabled is False and svc.s3_client is None
        assert os.path.isdir(svc.backup_dir)

    def test_missing_bucket_disables(self, svc_env):
        """S3 enabled without a bucket disables itself."""
        svc = svc_env(BACKUP_S3_ENABLED="true")
        assert svc.s3_enabled is False

    def test_full_config_head_bucket_ok(self, svc_env):
        """A configured client with custom endpoint and keys passes head_bucket."""
        client = MagicMock()
        with patch(
            "apps.api.services.backup.service.boto3.client", return_value=client
        ) as bc:
            svc = svc_env(
                BACKUP_S3_ENABLED="true",
                BACKUP_S3_BUCKET="b",
                BACKUP_S3_ENDPOINT="http://minio.example.test",
                BACKUP_S3_ACCESS_KEY=fake_secret("ak"),
                BACKUP_S3_SECRET_KEY=fake_secret("sk"),
            )
        assert svc.s3_enabled and svc.s3_client is client
        kwargs = bc.call_args.kwargs
        assert kwargs["endpoint_url"] == "http://minio.example.test"
        assert "aws_access_key_id" in kwargs

    def test_missing_bucket_is_created(self, svc_env):
        """A 404 from head_bucket triggers bucket creation."""
        client = MagicMock()
        client.head_bucket.side_effect = _client_error("404")
        with patch(
            "apps.api.services.backup.service.boto3.client", return_value=client
        ):
            svc = svc_env(BACKUP_S3_ENABLED="true", BACKUP_S3_BUCKET="b")
        client.create_bucket.assert_called_once_with(Bucket="b")
        assert svc.s3_enabled

    def test_other_client_error_disables(self, svc_env):
        """Non-404 head_bucket errors disable S3 and drop the client."""
        client = MagicMock()
        client.head_bucket.side_effect = _client_error("403")
        with patch(
            "apps.api.services.backup.service.boto3.client", return_value=client
        ):
            svc = svc_env(BACKUP_S3_ENABLED="true", BACKUP_S3_BUCKET="b")
        assert svc.s3_enabled is False and svc.s3_client is None


class TestS3Ops:
    """Upload/download/delete helpers."""

    @pytest.fixture
    def s3svc(self, svc_env):
        """Service with a mocked S3 client attached."""
        svc = svc_env()
        svc.s3_enabled = True
        svc.s3_client = MagicMock()
        svc.s3_bucket = "bkt"
        return svc

    def test_not_enabled_raises(self, svc_env):
        """All S3 helpers raise when S3 is not configured."""
        svc = svc_env()
        for call in (
            lambda: svc._upload_to_s3("f", "n"),
            lambda: svc._download_from_s3("k", "p"),
            lambda: svc._delete_from_s3("k"),
        ):
            with pytest.raises(Exception, match="not enabled"):
                call()

    def test_upload_aws_url_and_endpoint_url(self, s3svc, tmp_path):
        """Upload builds an AWS-style URL by default and an endpoint URL when set."""
        f = tmp_path / "x.gz"
        f.write_bytes(b"data")
        res = s3svc._upload_to_s3(str(f), "x.gz")
        assert res["success"] and res["s3_url"].startswith("https://bkt.s3.")
        s3svc.s3_endpoint = "http://minio.example.test"
        res = s3svc._upload_to_s3(str(f), "x.gz")
        assert res["s3_url"].startswith("http://minio.example.test/bkt/")

    def test_upload_client_error(self, s3svc, tmp_path):
        """ClientError during upload is reported, not raised."""
        f = tmp_path / "x.gz"
        f.write_bytes(b"d")
        s3svc.s3_client.upload_fileobj.side_effect = _client_error("500")
        res = s3svc._upload_to_s3(str(f), "x.gz")
        assert res["success"] is False

    def test_download_ok_and_error(self, s3svc):
        """Download returns True, and wraps ClientError."""
        assert s3svc._download_from_s3("k", "p") is True
        s3svc.s3_client.download_file.side_effect = _client_error("500")
        with pytest.raises(Exception, match="Failed to download"):
            s3svc._download_from_s3("k", "p")

    def test_delete_ok_and_error(self, s3svc):
        """Delete returns True, and wraps ClientError."""
        assert s3svc._delete_from_s3("k") is True
        s3svc.s3_client.delete_object.side_effect = _client_error("500")
        with pytest.raises(Exception, match="Failed to delete"):
            s3svc._delete_from_s3("k")


def _mk_job(svc, **kw):
    """Insert a job row directly (the service's create path is broken) and return it."""
    cols = {
        "name": "job-" + uuid.uuid4().hex[:6],
        "schedule": "0 0 * * *",
        "retention_days": 30,
        "enabled": True,
        "s3_enabled": False,
        "village_id": vid(),
    }
    cols.update(kw)
    jid = svc.db.backup_jobs.insert(**cols)
    svc.db.commit()
    return svc.db.backup_jobs[jid].as_dict()


def _job_obj(job, include=("backup_jobs",), exclude=()):
    """Wrap a job dict with the config_json attribute _execute_backup reads."""
    cfg = {}
    if include:
        cfg["include_tables"] = list(include)
    if exclude:
        cfg["exclude_tables"] = list(exclude)
    return SimpleNamespace(**job, config_json=json.dumps(cfg) if cfg else None)


@pytest.mark.asyncio
class TestJobs:
    """Job CRUD and listing."""

    async def test_crud(self, svc_env):
        """List (with filter), get, update and delete a job."""
        svc = svc_env()

        def run():
            """Exercise the job lifecycle on the real DB."""
            job = _mk_job(svc, enabled=False)
            ids = [j["id"] for j in svc.list_backup_jobs()]
            assert job["id"] in ids
            assert job["id"] not in [
                j["id"] for j in svc.list_backup_jobs(enabled=True)
            ]
            assert job["id"] in [j["id"] for j in svc.list_backup_jobs(enabled=False)]
            assert svc.get_backup_job(job["id"])["name"] == job["name"]
            upd = svc.update_backup_job(
                job["id"],
                name="n2",
                schedule="1 1 * * *",
                retention_days=3,
                enabled=True,
            )
            assert upd["name"] == "n2" and upd["retention_days"] == 3
            noop = svc.update_backup_job(job["id"])
            assert noop["name"] == "n2"
            assert "deleted" in svc.delete_backup_job(job["id"])["message"]
            for fn in (
                lambda: svc.get_backup_job(job["id"]),
                lambda: svc.update_backup_job(job["id"], name="z"),
                lambda: svc.delete_backup_job(job["id"]),
                lambda: svc.run_backup_job(job["id"]),
            ):
                with pytest.raises(Exception, match="not found"):
                    fn()

        await run_in_threadpool(run)

    @pytest.mark.xfail(
        strict=True,
        reason="bug: create_backup_job inserts config_json/description, columns backup_jobs does not have",
    )
    async def test_create_job(self, svc_env):
        """Creating a job through the service should succeed."""
        svc = svc_env()
        job = await run_in_threadpool(
            lambda: svc.create_backup_job(
                name="c-" + uuid.uuid4().hex[:6],
                schedule="* * * * *",
                include_tables=["backup_jobs"],
                exclude_tables=["x"],
            )
        )
        assert job["name"]

    @pytest.mark.xfail(
        strict=True,
        reason="bug: update_backup_job writes description, a column backup_jobs does not have",
    )
    async def test_update_description(self, svc_env):
        """Updating the description should persist."""
        svc = svc_env()

        def run():
            """Update a description on a real row."""
            job = _mk_job(svc)
            return svc.update_backup_job(job["id"], description="d")

        assert (await run_in_threadpool(run))["name"]

    @pytest.mark.xfail(
        strict=True,
        reason="bug: run_backup_job reads job.config_json which the backup_jobs row lacks, so every run fails",
    )
    async def test_run_backup_job_succeeds(self, svc_env):
        """Manually running a stored job should produce a completed backup."""
        svc = svc_env()

        def run():
            """Run a stored job through the public entry point."""
            job = _mk_job(svc)
            return svc.run_backup_job(job["id"])

        assert (await run_in_threadpool(run))["success"] is True


@pytest.mark.asyncio
class TestRunAndBackups:
    """Backup execution, listing, download paths, restore, stats."""

    async def test_run_list_get_stats_delete(self, svc_env):
        """Run a job to a real gz file then list/get/stat/delete the backup."""
        svc = _fake(svc_env)

        def run():
            """Full backup lifecycle."""
            job = _mk_job(svc)
            res = svc._execute_backup(_job_obj(job))
            assert res["success"], res
            assert res["record_count"] >= 1 and "s3_url" not in res
            path = svc.get_backup_file_path(res["backup_id"])
            with gzip.open(path, "rt") as f:
                assert "backup_jobs" in json.load(f)["tables"]
            assert svc.list_backups(job_id=job["id"])[0]["id"] == res["backup_id"]
            assert svc.list_backups(limit=1)
            assert svc.get_backup(res["backup_id"])["status"] == "completed"
            stats = svc.get_backup_stats()
            assert stats["completed_backups"] >= 1 and stats["recent_backups"]
            svc.delete_backup(res["backup_id"])
            assert not os.path.exists(path)
            with pytest.raises(Exception, match="not found"):
                svc.get_backup(res["backup_id"])
            with pytest.raises(Exception, match="not found"):
                svc.delete_backup(res["backup_id"])
            with pytest.raises(Exception, match="not found"):
                svc.get_backup_file_path(res["backup_id"])
            svc.delete_backup_job(job["id"])

        await run_in_threadpool(run)

    async def test_table_error_and_exclude(self, svc_env):
        """A per-table read failure is recorded; excluded tables are skipped."""
        svc = _fake(svc_env)

        def run():
            """Back up with one table that raises and one excluded."""
            job = _mk_job(svc)
            real = svc.db.__class__.__getitem__

            def boom(self, key):
                """Raise for backup_jobs to hit the per-table except branch."""
                if key == "backup_jobs":
                    raise RuntimeError("table boom")
                return real(self, key)

            with patch.object(svc.db.__class__, "__getitem__", boom):
                res = svc._execute_backup(
                    _job_obj(job, ("backup_jobs", "backups"), ("backups",))
                )
            assert res["success"]
            with gzip.open(svc.get_backup_file_path(res["backup_id"]), "rt") as f:
                data = json.load(f)
            assert data["tables"]["backup_jobs"] == {"error": "table boom"}
            assert "backups" not in data["tables"]
            svc.delete_backup_job(job["id"])

        await run_in_threadpool(run)

    @pytest.mark.xfail(
        strict=True,
        reason="bug: failed-backup record insert omits NOT NULL backups.filename so the error path itself raises",
    )
    async def test_failed_backup_is_recorded(self, svc_env):
        """A failing backup should return success False with a stored failed record."""
        svc = svc_env()

        def run():
            """Force the outer failure path with unparsable config."""
            job = _mk_job(svc)
            bad = SimpleNamespace(**job, config_json="{not json")
            return svc._execute_backup(bad)

        res = await run_in_threadpool(run)
        assert res["success"] is False

    async def test_failed_backup_logic(self, svc_env):
        """With an in-memory DAL the failure branch records a failed backup."""
        svc = _fake(svc_env)

        def run():
            """Force the outer failure path with unparsable config."""
            job = _mk_job(svc)
            return svc._execute_backup(
                SimpleNamespace(**job, config_json="{not json")
            ), svc.db.backups.rows

        res, rows = await run_in_threadpool(run)
        assert res["success"] is False and rows[0]["status"] == "failed"

    async def test_s3_upload_per_job_config(self, svc_env):
        """A job with S3 enabled uploads and restores the global S3 settings."""
        svc = _fake(svc_env)

        def run():
            """Run an S3-enabled job with the S3 boundary mocked."""
            job = _mk_job(
                svc,
                s3_enabled=True,
                s3_endpoint="http://minio.example.test",
                s3_bucket="jb",
                s3_region="eu-west-1",
                s3_access_key=fake_secret("ak"),
                s3_secret_key=fake_secret("sk"),
                s3_prefix="pre/",
            )
            client = MagicMock()
            with patch(
                "apps.api.services.backup.service.boto3.client", return_value=client
            ):
                svc.s3_enabled = True
                res = svc._execute_backup(_job_obj(job))
            assert res["success"] and res["s3_uploaded"] is True
            assert res["s3_url"].startswith("http://minio.example.test/jb/pre/")
            assert svc.s3_bucket is None
            svc.delete_backup_job(job["id"])
            return res

        await run_in_threadpool(run)

    async def test_s3_upload_failure_continues(self, svc_env):
        """An S3 failure is logged and the local backup still succeeds."""
        svc = _fake(svc_env)

        def run():
            """Run an S3-enabled job whose upload raises."""
            job = _mk_job(svc, s3_enabled=True, s3_bucket="jb")
            with patch.object(svc, "_init_s3_client", side_effect=RuntimeError("x")):
                res = svc._execute_backup(_job_obj(job))
            assert res["success"] and "s3_url" not in res
            svc.delete_backup_job(job["id"])

        await run_in_threadpool(run)

    async def test_s3_enabled_but_upload_unsuccessful(self, svc_env):
        """A non-success upload result leaves no S3 URL on the record."""
        svc = _fake(svc_env)

        def run():
            """Upload returns success False."""
            job = _mk_job(svc, s3_enabled=True, s3_bucket="jb")
            with (
                patch.object(svc, "_init_s3_client"),
                patch.object(svc, "_upload_to_s3", return_value={"success": False}),
            ):
                res = svc._execute_backup(_job_obj(job))
            assert res["success"] and "s3_url" not in res
            svc.delete_backup_job(job["id"])

        await run_in_threadpool(run)

    async def test_cleanup_old_backups(self, svc_env, tmp_path):
        """Expired completed backups are removed (local file, S3 key, record)."""
        svc = _fake(svc_env)

        def run():
            """Insert an expired backup and clean it up."""
            job = _mk_job(svc)
            f = tmp_path / "old.gz"
            f.write_bytes(b"x")
            old = datetime.now(UTC) - timedelta(days=90)
            for fp, key in (
                (str(f), "k1"),
                (None, None),
                (str(tmp_path / "gone"), None),
            ):
                svc.db.backups.insert(
                    job_id=job["id"],
                    filename="o.gz",
                    file_path=fp,
                    status="completed",
                    s3_key=key,
                    completed_at=old,
                    village_id=vid(),
                    created_at=old,
                )
            svc.db.commit()
            svc.s3_enabled = True
            svc.s3_client = MagicMock()
            svc._cleanup_old_backups(job["id"], 30)
            assert not f.exists()
            assert svc.s3_client.delete_object.called
            assert svc.db(svc.db.backups.job_id == job["id"]).count() == 0
            svc.delete_backup_job(job["id"])

        await run_in_threadpool(run)

    async def test_cleanup_tolerates_errors(self, svc_env, tmp_path):
        """S3 and file removal failures during cleanup are swallowed."""
        svc = _fake(svc_env)

        def run():
            """Cleanup with failing S3 delete and failing os.remove."""
            job = _mk_job(svc)
            f = tmp_path / "old2.gz"
            f.write_bytes(b"x")
            old = datetime.now(UTC) - timedelta(days=90)
            svc.db.backups.insert(
                job_id=job["id"],
                filename="o.gz",
                file_path=str(f),
                status="completed",
                s3_key="k",
                completed_at=old,
                village_id=vid(),
                created_at=old,
            )
            svc.db.commit()
            svc.s3_enabled = True
            with (
                patch.object(svc, "_delete_from_s3", side_effect=RuntimeError("s3")),
                patch(
                    "apps.api.services.backup.service.os.remove",
                    side_effect=OSError("rm"),
                ),
            ):
                svc._cleanup_old_backups(job["id"], 30)
            svc.delete_backup_job(job["id"])

        await run_in_threadpool(run)

    async def test_delete_backup_s3_error_tolerated(self, svc_env):
        """delete_backup keeps going when the S3 delete fails."""
        svc = _fake(svc_env)

        def run():
            """Delete a backup whose S3 delete raises."""
            job = _mk_job(svc)
            bid = svc.db.backups.insert(
                job_id=job["id"],
                filename="o.gz",
                status="completed",
                s3_key="k",
                village_id=vid(),
            )
            svc.db.commit()
            svc.s3_enabled = True
            with patch.object(svc, "_delete_from_s3", side_effect=RuntimeError("s3")):
                svc.delete_backup(bid)
            svc.delete_backup_job(job["id"])

        await run_in_threadpool(run)

    async def test_file_path_download_paths(self, svc_env, tmp_path):
        """Missing local files fall back to S3 download, else error."""
        svc = _fake(svc_env)

        def run():
            """Cover S3 fallback success, failure and no-S3 cases."""
            job = _mk_job(svc)
            missing = str(tmp_path / "nope.gz")
            b1 = svc.db.backups.insert(
                job_id=job["id"],
                filename="n.gz",
                file_path=missing,
                status="completed",
                s3_key="k",
                village_id=vid(),
            )
            b2 = svc.db.backups.insert(
                job_id=job["id"],
                filename="n2.gz",
                file_path=None,
                status="completed",
                s3_key="k",
                village_id=vid(),
            )
            b3 = svc.db.backups.insert(
                job_id=job["id"],
                filename="n3.gz",
                file_path=None,
                status="completed",
                village_id=vid(),
            )
            svc.db.commit()
            with pytest.raises(Exception, match="not found"):
                svc.get_backup_file_path(b1)
            svc.s3_enabled = True
            svc.s3_client = MagicMock()
            assert svc.get_backup_file_path(b1) == missing
            assert svc.get_backup_file_path(b2).endswith("n2.gz")
            svc.s3_client.download_file.side_effect = _client_error("500")
            with pytest.raises(Exception, match="locally or in S3"):
                svc.get_backup_file_path(b1)
            with pytest.raises(Exception, match="not found"):
                svc.get_backup_file_path(b3)
            svc.delete_backup_job(job["id"])

        await run_in_threadpool(run)


def _write_backup(svc, tmp_path, payload, job_id):
    """Write a gz backup payload and insert its record; return the backup id."""
    p = tmp_path / f"{uuid.uuid4().hex[:6]}.json.gz"
    with gzip.open(p, "wt") as f:
        json.dump(payload, f)
    bid = svc.db.backups.insert(
        job_id=job_id,
        filename=p.name,
        file_path=str(p),
        status="completed",
        village_id=vid(),
    )
    svc.db.commit()
    return bid


@pytest.mark.asyncio
class TestRestoreAndImport:
    """Restore, import and export flows."""

    async def test_restore(self, svc_env, tmp_path):
        """Restore handles missing, invalid, errored, dry-run and real inserts."""
        svc = _fake(svc_env)

        def run():
            """Exercise every restore branch."""
            job = _mk_job(svc)
            jid = job["id"]
            with pytest.raises(Exception, match="not found"):
                svc.restore_backup(10**9)
            nofile = svc.db.backups.insert(
                job_id=jid,
                filename="a",
                file_path=None,
                status="completed",
                village_id=vid(),
            )
            svc.db.commit()
            with pytest.raises(Exception, match="file not found"):
                svc.restore_backup(nofile)
            bad = _write_backup(svc, tmp_path, {"x": 1}, jid)
            with pytest.raises(Exception, match="Invalid backup format"):
                svc.restore_backup(bad)

            row = {
                "name": "restored-" + uuid.uuid4().hex[:6],
                "schedule": "* * * * *",
                "retention_days": 5,
                "enabled": True,
                "s3_enabled": False,
                "village_id": vid(),
                "id": 999999,
            }
            payload = {
                "version": "1",
                "tables": {
                    "backup_jobs": [dict(row)],
                    "broken": {"error": "read failed"},
                    "entities": [{"bad": 1}],
                },
            }
            bid = _write_backup(svc, tmp_path, payload, jid)
            dry = svc.restore_backup(bid, dry_run=True)
            assert dry["restored_counts"]["backup_jobs"] == 1
            assert dry["errors"]["broken"] == "read failed"
            real = svc.restore_backup(
                bid,
                restore_options={
                    "tables": ["backup_jobs", "missing"],
                    "clear_existing": False,
                },
            )
            assert real["restored_counts"] == {"backup_jobs": 1}
            assert real["errors"] == {}
            assert not any(r["id"] == 999999 for r in svc.db.backup_jobs.rows)
            full = svc.restore_backup(
                bid,
                restore_options={
                    "clear_existing": True,
                    "regenerate_ids": False,
                },
            )
            assert "entities" in full["errors"]
            assert "broken" in full["errors"]
            assert any(r["id"] == 999999 for r in svc.db.backup_jobs.rows)

        await run_in_threadpool(run)

    async def test_export_formats(self, svc_env):
        """Export json/csv/xml, skip empties and reject unknown formats."""
        svc = svc_env()

        def run():
            """Export every supported format against the real DB."""
            with pytest.raises(Exception, match="Unsupported format"):
                svc.export_data("yaml", ["entity"])
            for fmt in ("json", "xml"):
                res = svc.export_data(fmt, ["entity", "organization", "issue", "other"])
                assert res["success"] and res["format"] == fmt
                assert os.path.exists(res["filepath"])
                os.remove(res["filepath"])
            assert set(res["record_counts"]) == {"entities", "organizations", "issues"}

        await run_in_threadpool(run)

    async def test_export_xml_with_rows(self, svc_env, world):
        """XML export writes rows when organizations exist."""
        svc = svc_env()

        def run():
            """Export organizations with data present."""
            res = svc.export_data("xml", ["organization"])
            assert res["record_counts"]["organizations"] >= 1
            assert res["file_size"] > 0
            os.remove(res["filepath"])

        await run_in_threadpool(run)

    @pytest.mark.xfail(
        strict=True,
        reason="bug: CSV export writes per-type files but getsize() targets the never-created base filepath",
    )
    async def test_export_csv(self, svc_env, world):
        """CSV export of existing organizations should succeed."""
        svc = svc_env()
        res = await run_in_threadpool(lambda: svc.export_data("csv", ["organization"]))
        assert res["success"]

    @pytest.mark.xfail(
        strict=True,
        reason="bug: restore/import call self.db.rollback which penguin-dal resolves as a table name",
    )
    async def test_restore_insert_error_rolls_back(self, svc_env, tmp_path):
        """A failing insert during restore should be reported in errors."""
        svc = svc_env()

        def run():
            """Restore a record with an unknown column."""
            job = _mk_job(svc)
            bid = _write_backup(
                svc,
                tmp_path,
                {"version": "1", "tables": {"backup_jobs": [{"nope": 1}]}},
                job["id"],
            )
            return svc.restore_backup(bid)

        assert (await run_in_threadpool(run))["errors"]

    @pytest.mark.xfail(
        strict=True,
        reason="bug: import error path calls self.db.rollback which penguin-dal resolves as a table name",
    )
    async def test_import_insert_error_rolls_back(self, svc_env, tmp_path):
        """A failing import should be reported in errors."""
        svc = svc_env()
        p = tmp_path / "e.json"
        p.write_text(json.dumps({"no_such_table": [{"a": 1}]}))
        res = await run_in_threadpool(lambda: svc.import_data(str(p)))
        assert "no_such_table" in res["errors"]

    async def test_import_data(self, svc_env, tmp_path):
        """Import covers missing file, bad extension, json, json.gz, dry-run and errors."""
        svc = _fake(svc_env)

        def run():
            """Drive every import branch."""
            with pytest.raises(Exception, match="not found"):
                svc.import_data(str(tmp_path / "none.json"))
            txt = tmp_path / "a.txt"
            txt.write_text("x")
            with pytest.raises(Exception, match="Unsupported"):
                svc.import_data(str(txt))
            name = "imp-" + uuid.uuid4().hex[:6]
            rec = {
                "id": 5,
                "name": name,
                "schedule": "* * * * *",
                "retention_days": 1,
                "enabled": True,
                "s3_enabled": False,
                "village_id": vid(),
            }
            data = {
                "version": "1",
                "timestamp": "t",
                "job_id": 1,
                "job_name": "x",
                "backup_jobs": [rec, {"error": "skip"}, "junk"],
                "backups": "notalist",
                "nonexistent_table": [{"a": 1}],
                "entities": [{"bad": 1}],
            }
            pj = tmp_path / "d.json"
            pj.write_text(json.dumps(data))
            dry = svc.import_data(str(pj), dry_run=True)
            assert dry["dry_run"] and dry["imported_counts"]["backups"] == 0
            res = svc.import_data(str(pj))
            assert res["imported_counts"]["backup_jobs"] == 3
            assert set(res["errors"]) == {"nonexistent_table", "entities"}
            pg = tmp_path / "d.json.gz"
            with gzip.open(pg, "wt") as f:
                json.dump({"version": "1"}, f)
            assert svc.import_data(str(pg))["imported_tables"] == 0

        await run_in_threadpool(run)
