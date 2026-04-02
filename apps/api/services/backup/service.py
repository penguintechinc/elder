"""Backup & Data Management Service for Elder v1.2.0 (Phase 10)."""

# flake8: noqa: E501


import csv
import gzip
import json
import logging
import os
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError
from penguin_dal import DAL

logger = logging.getLogger(__name__)


class BackupService:
    """Service for backup and data management operations."""

    def __init__(self, db: DAL):
        """
        Initialize BackupService.

        Args:
            db: penguin-dal database instance
        """
        self.db = db
        self.backup_dir = os.getenv("BACKUP_DIR", "/tmp/elder/backups")

        # Ensure backup directory exists
        os.makedirs(self.backup_dir, exist_ok=True)

        # S3-compatible storage configuration
        self.s3_enabled = os.getenv("BACKUP_S3_ENABLED", "false").lower() == "true"
        self.s3_endpoint = os.getenv(
            "BACKUP_S3_ENDPOINT"
        )  # e.g., https://s3.amazonaws.com or https://minio.example.com
        self.s3_bucket = os.getenv("BACKUP_S3_BUCKET")
        self.s3_region = os.getenv("BACKUP_S3_REGION", "us-east-1")
        self.s3_access_key = os.getenv("BACKUP_S3_ACCESS_KEY")
        self.s3_secret_key = os.getenv("BACKUP_S3_SECRET_KEY")
        self.s3_prefix = os.getenv(
            "BACKUP_S3_PREFIX", "elder/backups/"
        )  # Prefix for all backup objects

        # Initialize S3 client if enabled
        self.s3_client = None
        if self.s3_enabled:
            self._init_s3_client()

    # ===========================
    # S3 Storage Backend
    # ===========================

    def _init_s3_client(self) -> None:
        """Initialize S3 client for S3-compatible storage."""
        try:
            if not self.s3_bucket:
                logger.warning("BACKUP_S3_BUCKET not configured, disabling S3 backups")
                self.s3_enabled = False
                return

            # Create S3 client with custom endpoint support (for MinIO, Wasabi, etc.)
            client_config = {
                "region_name": self.s3_region,
            }

            if self.s3_access_key and self.s3_secret_key:
                client_config["aws_access_key_id"] = self.s3_access_key
                client_config["aws_secret_access_key"] = self.s3_secret_key

            if self.s3_endpoint:
                # Custom endpoint for S3-compatible services
                client_config["endpoint_url"] = self.s3_endpoint

            self.s3_client = boto3.client("s3", **client_config)

            # Test connection and bucket access
            try:
                self.s3_client.head_bucket(Bucket=self.s3_bucket)
                logger.info(
                    f"S3 backup enabled: bucket={self.s3_bucket}, endpoint={self.s3_endpoint or 'AWS'}"
                )
            except ClientError as e:
                if e.response["Error"]["Code"] == "404":
                    # Bucket doesn't exist, try to create it
                    logger.info(f"Creating S3 bucket: {self.s3_bucket}")
                    self.s3_client.create_bucket(Bucket=self.s3_bucket)
                else:
                    raise

        except Exception as e:
            logger.error(f"Failed to initialize S3 client: {e}")
            self.s3_enabled = False
            self.s3_client = None

    def _upload_to_s3(self, filepath: str, filename: str) -> Dict[str, Any]:
        """
        Upload backup file to S3-compatible storage.

        Args:
            filepath: Local file path
            filename: Backup filename

        Returns:
            Upload result with S3 URL and metadata

        Raises:
            Exception: If upload fails
        """
        if not self.s3_enabled or not self.s3_client:
            raise Exception("S3 storage not enabled or configured")

        try:
            s3_key = f"{self.s3_prefix}{filename}"

            # Upload file with metadata
            with open(filepath, "rb") as f:
                self.s3_client.upload_fileobj(
                    f,
                    self.s3_bucket,
                    s3_key,
                    ExtraArgs={
                        "Metadata": {
                            "elder-version": "1.2.0",
                            "upload-timestamp": datetime.utcnow().isoformat(),
                        }
                    },
                )

            # Generate S3 URL
            if self.s3_endpoint:
                # Custom endpoint URL
                s3_url = f"{self.s3_endpoint}/{self.s3_bucket}/{s3_key}"
            else:
                # AWS S3 URL
                s3_url = f"https://{self.s3_bucket}.s3.{self.s3_region}.amazonaws.com/{s3_key}"

            logger.info(f"Backup uploaded to S3: {s3_url}")

            return {
                "success": True,
                "s3_url": s3_url,
                "s3_bucket": self.s3_bucket,
                "s3_key": s3_key,
                "s3_region": self.s3_region,
            }

        except ClientError as e:
            logger.error(f"S3 upload failed: {e}")
            return {"success": False, "error": str(e)}

    def _download_from_s3(self, s3_key: str, local_filepath: str) -> bool:
        """
        Download backup file from S3-compatible storage.

        Args:
            s3_key: S3 object key
            local_filepath: Local destination path

        Returns:
            True if successful

        Raises:
            Exception: If download fails
        """
        if not self.s3_enabled or not self.s3_client:
            raise Exception("S3 storage not enabled or configured")

        try:
            self.s3_client.download_file(self.s3_bucket, s3_key, local_filepath)

            logger.info(f"Backup downloaded from S3: {s3_key}")
            return True

        except ClientError as e:
            logger.error(f"S3 download failed: {e}")
            raise Exception(f"Failed to download from S3: {e}")

    def _delete_from_s3(self, s3_key: str) -> bool:
        """
        Delete backup file from S3-compatible storage.

        Args:
            s3_key: S3 object key

        Returns:
            True if successful

        Raises:
            Exception: If deletion fails
        """
        if not self.s3_enabled or not self.s3_client:
            raise Exception("S3 storage not enabled or configured")

        try:
            self.s3_client.delete_object(Bucket=self.s3_bucket, Key=s3_key)

            logger.info(f"Backup deleted from S3: {s3_key}")
            return True

        except ClientError as e:
            logger.error(f"S3 deletion failed: {e}")
            raise Exception(f"Failed to delete from S3: {e}")

    # ===========================
    # Backup Job Management
    # ===========================

    def list_backup_jobs(self, enabled: Optional[bool] = None) -> List[Dict[str, Any]]:
        """
        List all backup jobs.

        Args:
            enabled: Filter by enabled status

        Returns:
            List of backup jobs
        """
        query = self.db.backup_jobs.id > 0

        if enabled is not None:
            query &= self.db.backup_jobs.enabled == enabled

        jobs = self.db(query).select(orderby=self.db.backup_jobs.created_at)

        return [j.as_dict() for j in jobs]

    def get_backup_job(self, job_id: int) -> Dict[str, Any]:
        """
        Get backup job details.

        Args:
            job_id: Backup job ID

        Returns:
            Backup job dictionary

        Raises:
            Exception: If job not found
        """
        job = self.db.backup_jobs[job_id]

        if not job:
            raise Exception(f"Backup job {job_id} not found")

        return job.as_dict()

    def create_backup_job(
        self,
        name: str,
        schedule: Optional[str] = None,
        retention_days: int = 30,
        enabled: bool = True,
        description: Optional[str] = None,
        include_tables: Optional[List[str]] = None,
        exclude_tables: Optional[List[str]] = None,
        s3_enabled: bool = False,
        s3_endpoint: Optional[str] = None,
        s3_bucket: Optional[str] = None,
        s3_region: Optional[str] = None,
        s3_access_key: Optional[str] = None,
        s3_secret_key: Optional[str] = None,
        s3_prefix: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new backup job.

        Args:
            name: Job name
            schedule: Cron schedule (optional for manual jobs)
            retention_days: Number of days to retain backups
            enabled: Enable/disable job
            description: Optional description
            include_tables: Tables to include (None = all)
            exclude_tables: Tables to exclude
            s3_enabled: Enable S3 storage for this job
            s3_endpoint: S3 endpoint URL (optional, for custom S3-compatible services)
            s3_bucket: S3 bucket name
            s3_region: S3 region
            s3_access_key: S3 access key ID
            s3_secret_key: S3 secret access key
            s3_prefix: S3 key prefix

        Returns:
            Created backup job dictionary
        """
        config = {}
        if include_tables:
            config["include_tables"] = include_tables
        if exclude_tables:
            config["exclude_tables"] = exclude_tables

        job_id = self.db.backup_jobs.insert(
            name=name,
            schedule=schedule,
            retention_days=retention_days,
            enabled=enabled,
            description=description,
            config_json=json.dumps(config) if config else None,
            s3_enabled=s3_enabled,
            s3_endpoint=s3_endpoint,
            s3_bucket=s3_bucket,
            s3_region=s3_region,
            s3_access_key=s3_access_key,
            s3_secret_key=s3_secret_key,
            s3_prefix=s3_prefix,
            created_at=datetime.utcnow(),
        )

        self.db.commit()

        job = self.db.backup_jobs[job_id]
        return job.as_dict()

    def update_backup_job(
        self,
        job_id: int,
        name: Optional[str] = None,
        schedule: Optional[str] = None,
        retention_days: Optional[int] = None,
        enabled: Optional[bool] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update backup job configuration.

        Args:
            job_id: Backup job ID
            name: New name
            schedule: New schedule
            retention_days: New retention period
            enabled: New enabled status
            description: New description

        Returns:
            Updated backup job dictionary

        Raises:
            Exception: If job not found
        """
        job = self.db.backup_jobs[job_id]

        if not job:
            raise Exception(f"Backup job {job_id} not found")

        update_data = {"updated_at": datetime.utcnow()}

        if name is not None:
            update_data["name"] = name

        if schedule is not None:
            update_data["schedule"] = schedule

        if retention_days is not None:
            update_data["retention_days"] = retention_days

        if enabled is not None:
            update_data["enabled"] = enabled

        if description is not None:
            update_data["description"] = description

        self.db(self.db.backup_jobs.id == job_id).update(**update_data)
        self.db.commit()

        job = self.db.backup_jobs[job_id]
        return job.as_dict()

    def delete_backup_job(self, job_id: int) -> Dict[str, str]:
        """
        Delete backup job.

        Args:
            job_id: Backup job ID

        Returns:
            Success message

        Raises:
            Exception: If job not found
        """
        job = self.db.backup_jobs[job_id]

        if not job:
            raise Exception(f"Backup job {job_id} not found")

        # Delete associated backups
        self.db(self.db.backups.job_id == job_id).delete()

        # Delete job
        self.db(self.db.backup_jobs.id == job_id).delete()
        self.db.commit()

        return {"message": "Backup job deleted successfully"}

    def run_backup_job(self, job_id: int) -> Dict[str, Any]:
        """
        Manually trigger a backup job.

        Args:
            job_id: Backup job ID

        Returns:
            Backup execution result

        Raises:
            Exception: If job not found
        """
        job = self.db.backup_jobs[job_id]

        if not job:
            raise Exception(f"Backup job {job_id} not found")

        # Update last run time
        self.db(self.db.backup_jobs.id == job_id).update(last_run_at=datetime.utcnow())
        self.db.commit()

        # Execute backup
        return self._execute_backup(job)

    # ===========================
    # Backup Execution Methods
    # ===========================

    def _execute_backup(self, job: Any) -> Dict[str, Any]:
        """
        Execute a backup job.

        Args:
            job: Backup job record

        Returns:
            Backup execution result
        """
        try:
            start_time = datetime.utcnow()

            # Get config
            config = {}
            if job.config_json:
                config = json.loads(job.config_json)

            include_tables = config.get("include_tables")
            exclude_tables = config.get("exclude_tables", [])

            # Get all table names
            tables = list(self.db.tables)

            # Filter tables
            if include_tables:
                tables = [t for t in tables if t in include_tables]

            tables = [t for t in tables if t not in exclude_tables]

            # Create backup data structure
            backup_data = {
                "version": "1.2.0",
                "timestamp": start_time.isoformat(),
                "job_id": job.id,
                "job_name": job.name,
                "tables": {},
            }

            total_records = 0

            # Export each table
            for table_name in tables:
                try:
                    table = self.db[table_name]
                    rows = self.db(table.id > 0).select()

                    # Convert rows to dictionaries
                    table_data = [row.as_dict() for row in rows]
                    backup_data["tables"][table_name] = table_data
                    total_records += len(table_data)

                except Exception as e:
                    backup_data["tables"][table_name] = {"error": str(e)}

            # Generate filename
            filename = f"backup_{job.id}_{start_time.strftime('%Y%m%d_%H%M%S')}.json.gz"
            filepath = os.path.join(self.backup_dir, filename)

            # Write compressed backup
            with gzip.open(filepath, "wt", encoding="utf-8") as f:
                json.dump(backup_data, f, indent=2, default=str)

            # Get file size
            file_size = os.path.getsize(filepath)

            end_time = datetime.utcnow()
            duration_seconds = (end_time - start_time).total_seconds()

            # Upload to S3 if enabled (check per-job config first, then global)
            s3_url = None
            s3_key = None
            use_s3 = job.s3_enabled if hasattr(job, "s3_enabled") else self.s3_enabled

            if use_s3:
                try:
                    # Use per-job S3 config if available, otherwise use global config
                    if hasattr(job, "s3_enabled") and job.s3_enabled:
                        # Temporarily override S3 settings with job-specific config
                        original_s3_config = {
                            "endpoint": self.s3_endpoint,
                            "bucket": self.s3_bucket,
                            "region": self.s3_region,
                            "access_key": self.s3_access_key,
                            "secret_key": self.s3_secret_key,
                            "prefix": self.s3_prefix,
                        }

                        self.s3_endpoint = job.s3_endpoint or self.s3_endpoint
                        self.s3_bucket = job.s3_bucket or self.s3_bucket
                        self.s3_region = job.s3_region or self.s3_region
                        self.s3_access_key = job.s3_access_key or self.s3_access_key
                        self.s3_secret_key = job.s3_secret_key or self.s3_secret_key
                        self.s3_prefix = job.s3_prefix or self.s3_prefix

                        # Re-initialize S3 client with job-specific config
                        self._init_s3_client()

                    upload_result = self._upload_to_s3(filepath, filename)
                    if upload_result.get("success"):
                        s3_url = upload_result.get("s3_url")
                        s3_key = upload_result.get("s3_key")
                        logger.info(f"Backup uploaded to S3: {s3_url}")

                    # Restore original S3 config if we overrode it
                    if hasattr(job, "s3_enabled") and job.s3_enabled:
                        self.s3_endpoint = original_s3_config["endpoint"]
                        self.s3_bucket = original_s3_config["bucket"]
                        self.s3_region = original_s3_config["region"]
                        self.s3_access_key = original_s3_config["access_key"]
                        self.s3_secret_key = original_s3_config["secret_key"]
                        self.s3_prefix = original_s3_config["prefix"]
                        self._init_s3_client()

                except Exception as e:
                    logger.error(f"S3 upload failed, continuing with local backup: {e}")

            # Create backup record
            backup_id = self.db.backups.insert(
                job_id=job.id,
                filename=filename,
                file_path=filepath,
                file_size=file_size,
                record_count=total_records,
                status="completed",
                started_at=start_time,
                completed_at=end_time,
                duration_seconds=duration_seconds,
                s3_url=s3_url,
                s3_key=s3_key,
            )

            self.db.commit()

            # Clean up old backups based on retention policy
            self._cleanup_old_backups(job.id, job.retention_days)

            result = {
                "success": True,
                "backup_id": backup_id,
                "filename": filename,
                "file_size": file_size,
                "record_count": total_records,
                "duration_seconds": duration_seconds,
            }

            # Add S3 info if uploaded
            if s3_url:
                result["s3_url"] = s3_url
                result["s3_uploaded"] = True

            return result

        except Exception as e:
            # Record failed backup
            backup_id = self.db.backups.insert(
                job_id=job.id,
                status="failed",
                error_message=str(e),
                started_at=datetime.utcnow(),
            )
            self.db.commit()

            return {"success": False, "backup_id": backup_id, "error": str(e)}

    def _cleanup_old_backups(self, job_id: int, retention_days: int) -> None:
        """
        Clean up old backups based on retention policy.

        Args:
            job_id: Backup job ID
            retention_days: Retention period in days
        """
        cutoff_date = datetime.utcnow() - timedelta(days=retention_days)

        old_backups = self.db(
            (self.db.backups.job_id == job_id)
            & (self.db.backups.completed_at < cutoff_date)
            & (self.db.backups.status == "completed")
        ).select()

        for backup in old_backups:
            # Delete from S3 if exists
            if self.s3_enabled and backup.s3_key:
                try:
                    self._delete_from_s3(backup.s3_key)
                except Exception as e:
                    logger.error(f"Failed to delete backup from S3: {e}")

            # Delete local file
            if backup.file_path and os.path.exists(backup.file_path):
                try:
                    os.remove(backup.file_path)
                except Exception as e:
                    logger.error(f"Failed to delete local backup file: {e}")

            # Delete record
            self.db(self.db.backups.id == backup.id).delete()

        self.db.commit()

    # ===========================
    # Backup Management Methods
    # ===========================

    def list_backups(
        self, job_id: Optional[int] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        List all backups.

        Args:
            job_id: Filter by backup job
            limit: Maximum results

        Returns:
            List of backups
        """
        query = self.db.backups.id > 0

        if job_id is not None:
            query &= self.db.backups.job_id == job_id

        backups = self.db(query).select(
            orderby=~self.db.backups.completed_at, limitby=(0, limit)
        )

        return [b.as_dict() for b in backups]

    def get_backup(self, backup_id: int) -> Dict[str, Any]:
        """
        Get backup details.

        Args:
            backup_id: Backup ID

        Returns:
            Backup dictionary

        Raises:
            Exception: If backup not found
        """
        backup = self.db.backups[backup_id]

        if not backup:
            raise Exception(f"Backup {backup_id} not found")

        return backup.as_dict()

    def delete_backup(self, backup_id: int) -> Dict[str, str]:
        """
        Delete backup file.

        Args:
            backup_id: Backup ID

        Returns:
            Success message

        Raises:
            Exception: If backup not found
        """
        backup = self.db.backups[backup_id]

        if not backup:
            raise Exception(f"Backup {backup_id} not found")

        # Delete from S3 if exists
        if self.s3_enabled and backup.s3_key:
            try:
                self._delete_from_s3(backup.s3_key)
            except Exception as e:
                logger.error(f"Failed to delete backup from S3: {e}")

        # Delete local file
        if backup.file_path and os.path.exists(backup.file_path):
            os.remove(backup.file_path)

        # Delete record
        self.db(self.db.backups.id == backup_id).delete()
        self.db.commit()

        return {"message": "Backup deleted successfully"}

    def get_backup_file_path(self, backup_id: int) -> str:
        """
        Get backup file path for download.

        Args:
            backup_id: Backup ID

        Returns:
            File path

        Raises:
            Exception: If backup not found or file missing
        """
        backup = self.db.backups[backup_id]

        if not backup:
            raise Exception(f"Backup {backup_id} not found")

        # Check if local file exists
        if backup.file_path and os.path.exists(backup.file_path):
            return backup.file_path

        # Try to download from S3 if local file missing
        if self.s3_enabled and backup.s3_key:
            logger.info(
                f"Local backup file not found, downloading from S3: {backup.s3_key}"
            )
            try:
                # Download to original path or temp location
                download_path = backup.file_path or os.path.join(
                    tempfile.gettempdir(), backup.filename
                )
                self._download_from_s3(backup.s3_key, download_path)
                return download_path
            except Exception as e:
                logger.error(f"Failed to download backup from S3: {e}")
                raise Exception(f"Backup file not found locally or in S3")

        raise Exception(f"Backup file not found")

    # ===========================
    # Restore Operations
    # ===========================

    def restore_backup(
        self,
        backup_id: int,
        dry_run: bool = False,
        restore_options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Restore from backup.

        Args:
            backup_id: Backup ID
            dry_run: Test restore without committing changes
            restore_options: Optional restore configuration

        Returns:
            Restore result

        Raises:
            Exception: If backup not found or restore fails
        """
        backup = self.db.backups[backup_id]

        if not backup:
            raise Exception(f"Backup {backup_id} not found")

        if not backup.file_path or not os.path.exists(backup.file_path):
            raise Exception(f"Backup file not found")

        # Load backup data
        with gzip.open(backup.file_path, "rt", encoding="utf-8") as f:
            backup_data = json.load(f)

        # Validate backup format
        if "version" not in backup_data or "tables" not in backup_data:
            raise Exception("Invalid backup format")

        restore_options = restore_options or {}
        tables_to_restore = restore_options.get(
            "tables", list(backup_data["tables"].keys())
        )

        restored_counts = {}
        errors = {}

        for table_name in tables_to_restore:
            if table_name not in backup_data["tables"]:
                continue

            if (
                isinstance(backup_data["tables"][table_name], dict)
                and "error" in backup_data["tables"][table_name]
            ):
                errors[table_name] = backup_data["tables"][table_name]["error"]
                continue

            try:
                table_data = backup_data["tables"][table_name]

                if not dry_run:
                    # Clear existing data if requested
                    if restore_options.get("clear_existing", False):
                        self.db(self.db[table_name].id > 0).delete()

                    # Insert records
                    for record in table_data:
                        # Remove id if exists to let DB auto-generate
                        if "id" in record and restore_options.get(
                            "regenerate_ids", True
                        ):
                            del record["id"]

                        self.db[table_name].insert(**record)

                    self.db.commit()

                restored_counts[table_name] = len(table_data)

            except Exception as e:
                errors[table_name] = str(e)
                if not dry_run:
                    self.db.rollback()

        return {
            "backup_id": backup_id,
            "dry_run": dry_run,
            "restored_tables": len(restored_counts),
            "total_records": sum(restored_counts.values()),
            "restored_counts": restored_counts,
            "errors": errors,
        }

    # ===========================
    # Export Operations
    # ===========================

    def export_data(
        self,
        format: str,
        resource_types: List[str],
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Export data to various formats.

        Args:
            format: Export format (json, csv, xml)
            resource_types: Resource types to export
            filters: Optional filters

        Returns:
            Export result with file path

        Raises:
            Exception: If export fails
        """
        if format not in ["json", "csv", "xml"]:
            raise Exception(f"Unsupported format: {format}")

        # Collect data
        export_data = {}

        for resource_type in resource_types:
            if resource_type == "entity":
                entities = self.db(self.db.entities.id > 0).select()
                export_data["entities"] = [e.as_dict() for e in entities]

            elif resource_type == "organization":
                orgs = self.db(self.db.organizations.id > 0).select()
                export_data["organizations"] = [o.as_dict() for o in orgs]

            elif resource_type == "issue":
                issues = self.db(self.db.issues.id > 0).select()
                export_data["issues"] = [i.as_dict() for i in issues]

        # Generate filename
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"export_{timestamp}.{format}"
        filepath = os.path.join(tempfile.gettempdir(), filename)

        # Write export file
        if format == "json":
            with open(filepath, "w") as f:
                json.dump(export_data, f, indent=2, default=str)

        elif format == "csv":
            # CSV export for each resource type
            for resource_type, records in export_data.items():
                if not records:
                    continue

                csv_filename = f"export_{resource_type}_{timestamp}.csv"
                csv_filepath = os.path.join(tempfile.gettempdir(), csv_filename)

                with open(csv_filepath, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=records[0].keys())
                    writer.writeheader()
                    writer.writerows(records)

        elif format == "xml":
            root = ET.Element("export")
            root.set("timestamp", timestamp)

            for resource_type, records in export_data.items():
                type_elem = ET.SubElement(root, resource_type + "s")

                for record in records:
                    record_elem = ET.SubElement(type_elem, resource_type)
                    for key, value in record.items():
                        field_elem = ET.SubElement(record_elem, key)
                        field_elem.text = str(value) if value is not None else ""

            tree = ET.ElementTree(root)
            tree.write(filepath, encoding="utf-8", xml_declaration=True)

        file_size = os.path.getsize(filepath)

        return {
            "success": True,
            "format": format,
            "filename": filename,
            "filepath": filepath,
            "file_size": file_size,
            "resource_types": resource_types,
            "record_counts": {k: len(v) for k, v in export_data.items()},
        }

    # ===========================
    # Import Operations
    # ===========================

    def import_data(self, filepath: str, dry_run: bool = False) -> Dict[str, Any]:
        """
        Import data from file.

        Args:
            filepath: Path to import file
            dry_run: Test import without committing

        Returns:
            Import result

        Raises:
            Exception: If import fails
        """
        if not os.path.exists(filepath):
            raise Exception(f"Import file not found: {filepath}")

        # Detect format from extension
        if filepath.endswith(".json"):
            with open(filepath, "r") as f:
                import_data = json.load(f)

        elif filepath.endswith(".json.gz"):
            with gzip.open(filepath, "rt", encoding="utf-8") as f:
                import_data = json.load(f)

        else:
            raise Exception("Unsupported file format. Use .json or .json.gz")

        imported_counts = {}
        errors = {}

        # Import each resource type
        for resource_type, records in import_data.items():
            if resource_type in ["version", "timestamp", "job_id", "job_name"]:
                continue

            try:
                if not dry_run:
                    for record in records:
                        if isinstance(record, dict) and "error" not in record:
                            # Remove id to let DB auto-generate
                            if "id" in record:
                                del record["id"]

                            self.db[resource_type].insert(**record)

                    self.db.commit()

                imported_counts[resource_type] = (
                    len(records) if isinstance(records, list) else 0
                )

            except Exception as e:
                errors[resource_type] = str(e)
                if not dry_run:
                    self.db.rollback()

        return {
            "dry_run": dry_run,
            "imported_tables": len(imported_counts),
            "total_records": sum(imported_counts.values()),
            "imported_counts": imported_counts,
            "errors": errors,
        }

    # ===========================
    # Storage Statistics
    # ===========================

    def get_backup_stats(self) -> Dict[str, Any]:
        """
        Get backup and storage statistics.

        Returns:
            Storage statistics
        """
        total_backups = self.db(self.db.backups.id > 0).count()
        completed_backups = self.db(self.db.backups.status == "completed").count()
        failed_backups = self.db(self.db.backups.status == "failed").count()

        # Calculate total size
        backups = self.db(self.db.backups.status == "completed").select()
        total_size = sum(b.file_size or 0 for b in backups)

        # Get recent backups
        recent = self.db(self.db.backups.id > 0).select(
            orderby=~self.db.backups.completed_at, limitby=(0, 5)
        )

        return {
            "total_backups": total_backups,
            "completed_backups": completed_backups,
            "failed_backups": failed_backups,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "backup_directory": self.backup_dir,
            "recent_backups": [b.as_dict() for b in recent],
        }
