"""A tiny in-process SAML Identity Provider for tests.

Generates a real RSA key + self-signed X.509 certificate and builds *real*
XML-DSig-signed SAML Responses with xmlsec, so the Service Provider code under
test exercises its genuine signature verification path -- nothing in the
validation chain is mocked. Knobs on ``ResponseSpec`` let a test produce every
kind of bad assertion (expired, wrong audience/recipient/destination, wrong
InResponseTo, unsigned, wrong signer, wrapped, ...).
"""

import base64
import datetime
import time
import uuid
import zlib
from dataclasses import dataclass, field
from xml.sax.saxutils import escape, quoteattr

import xmlsec
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree

NS_SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
NS_SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"


@dataclass(slots=True, frozen=True)
class IdpKeys:
    """An IdP signing identity: PEM private key and PEM certificate."""

    key_pem: bytes
    cert_pem: bytes

    @property
    def cert_b64(self) -> str:
        """Certificate body without PEM armor (how admins paste it into the UI)."""
        lines = self.cert_pem.decode().strip().splitlines()
        return "".join(lines[1:-1])


def generate_keys(common_name: str = "test-idp") -> IdpKeys:
    """Generate a fresh RSA-2048 key and self-signed certificate."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return IdpKeys(
        key_pem=key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        cert_pem=cert.public_bytes(serialization.Encoding.PEM),
    )


def fmt_time(epoch: float) -> str:
    """Format epoch seconds as a SAML xs:dateTime (UTC, Z suffix)."""
    return datetime.datetime.fromtimestamp(epoch, datetime.UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


@dataclass(slots=True)
class ResponseSpec:
    """Every field of a SAML Response a test may want to control."""

    sp_entity_id: str
    acs_url: str
    in_response_to: str | None
    idp_entity_id: str = "https://idp.example.com/metadata"
    name_id: str = "jane.doe@example.com"
    attributes: dict[str, list[str]] = field(
        default_factory=lambda: {
            "email": ["jane.doe@example.com"],
            "name": ["Jane Doe"],
        }
    )
    audience: str | None = "__sp__"  # "__sp__" -> sp_entity_id; None -> no restriction
    destination: str | None = "__acs__"  # "__acs__" -> acs_url; None -> omitted
    recipient: str | None = "__acs__"
    scd_in_response_to: str | None = "__irt__"  # "__irt__" -> in_response_to
    not_before: float | None = None  # defaults to now - 30
    not_on_or_after: float | None = None  # defaults to now + 300
    scd_not_on_or_after: float | None = None  # defaults to now + 300
    include_conditions_not_on_or_after: bool = True
    include_scd_not_on_or_after: bool = True
    include_name_id: bool = True
    assertion_id: str = field(default_factory=lambda: f"_a{uuid.uuid4().hex}")
    response_id: str = field(default_factory=lambda: f"_r{uuid.uuid4().hex}")
    status: str = SUCCESS
    sign: str = "assertion"  # assertion | response | both | none
    sig_alg: str = "sha256"  # sha256 | sha1
    session_index: str = "_sess1"
    include_authn_statement: bool = True


def _resolve(value: str | None, marker: str, actual: str | None) -> str | None:
    """Resolve the ``__sp__``/``__acs__``/``__irt__`` markers to concrete values.

    A marker followed by a suffix (``"__acs__2"``) yields ``actual + suffix`` --
    handy for building the numeric-prefix sibling of a tenant's URL.
    """
    if value is not None and value.startswith(marker):
        return (actual or "") + value[len(marker) :]
    return value


def build_response_xml(spec: ResponseSpec) -> etree._Element:
    """Render an *unsigned* SAML Response described by ``spec`` as an lxml tree."""
    now = time.time()
    nb = spec.not_before if spec.not_before is not None else now - 30
    nooa = spec.not_on_or_after if spec.not_on_or_after is not None else now + 300
    scd_nooa = (
        spec.scd_not_on_or_after if spec.scd_not_on_or_after is not None else now + 300
    )
    audience = _resolve(spec.audience, "__sp__", spec.sp_entity_id)
    destination = _resolve(spec.destination, "__acs__", spec.acs_url)
    recipient = _resolve(spec.recipient, "__acs__", spec.acs_url)
    scd_irt = _resolve(spec.scd_in_response_to, "__irt__", spec.in_response_to)

    resp_attrs = f'ID="{spec.response_id}" Version="2.0" IssueInstant="{fmt_time(now)}"'
    if destination is not None:
        resp_attrs += f" Destination={quoteattr(destination)}"
    if spec.in_response_to is not None:
        resp_attrs += f" InResponseTo={quoteattr(spec.in_response_to)}"

    scd_attrs = (
        f'NotOnOrAfter="{fmt_time(scd_nooa)}"'
        if spec.include_scd_not_on_or_after
        else ""
    )
    if recipient is not None:
        scd_attrs += f" Recipient={quoteattr(recipient)}"
    if scd_irt is not None:
        scd_attrs += f" InResponseTo={quoteattr(scd_irt)}"

    cond_attrs = f'NotBefore="{fmt_time(nb)}"'
    if spec.include_conditions_not_on_or_after:
        cond_attrs += f' NotOnOrAfter="{fmt_time(nooa)}"'
    audience_xml = (
        f"<saml:AudienceRestriction><saml:Audience>{escape(audience)}"
        "</saml:Audience></saml:AudienceRestriction>"
        if audience is not None
        else ""
    )
    authn_xml = (
        f'<saml:AuthnStatement AuthnInstant="{fmt_time(now)}" '
        f'SessionIndex="{spec.session_index}"><saml:AuthnContext>'
        "<saml:AuthnContextClassRef>"
        "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport"
        "</saml:AuthnContextClassRef></saml:AuthnContext></saml:AuthnStatement>"
        if spec.include_authn_statement
        else ""
    )
    attr_xml = "".join(
        f"<saml:Attribute Name={quoteattr(name)}>"
        + "".join(
            f"<saml:AttributeValue>{escape(v)}</saml:AttributeValue>" for v in vals
        )
        + "</saml:Attribute>"
        for name, vals in spec.attributes.items()
    )
    attr_stmt = (
        f"<saml:AttributeStatement>{attr_xml}</saml:AttributeStatement>"
        if attr_xml
        else ""
    )

    name_id_xml = (
        '<saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">'
        f"{escape(spec.name_id)}</saml:NameID>"
        if spec.include_name_id
        else ""
    )
    xml = (
        f'<samlp:Response xmlns:samlp="{NS_SAMLP}" xmlns:saml="{NS_SAML}" {resp_attrs}>'
        f"<saml:Issuer>{escape(spec.idp_entity_id)}</saml:Issuer>"
        f'<samlp:Status><samlp:StatusCode Value="{spec.status}"/></samlp:Status>'
        f'<saml:Assertion ID="{spec.assertion_id}" Version="2.0" '
        f'IssueInstant="{fmt_time(now)}">'
        f"<saml:Issuer>{escape(spec.idp_entity_id)}</saml:Issuer>"
        "<saml:Subject>"
        f"{name_id_xml}"
        f'<saml:SubjectConfirmation Method="{BEARER}">'
        f"<saml:SubjectConfirmationData {scd_attrs}/>"
        "</saml:SubjectConfirmation></saml:Subject>"
        f"<saml:Conditions {cond_attrs}>{audience_xml}</saml:Conditions>"
        f"{authn_xml}{attr_stmt}"
        "</saml:Assertion></samlp:Response>"
    )
    return etree.fromstring(xml.encode())


def sign_element(element: etree._Element, keys: IdpKeys, *, sha1: bool = False) -> None:
    """Add an enveloped XML-DSig signature (after the Issuer) to ``element``."""
    xmlsec.tree.add_ids(element, ["ID"])
    sig_transform = xmlsec.Transform.RSA_SHA1 if sha1 else xmlsec.Transform.RSA_SHA256
    digest = xmlsec.Transform.SHA1 if sha1 else xmlsec.Transform.SHA256
    signature = xmlsec.template.create(
        element, xmlsec.Transform.EXCL_C14N, sig_transform, ns="ds"
    )
    ref = xmlsec.template.add_reference(signature, digest, uri="#" + element.get("ID"))
    xmlsec.template.add_transform(ref, xmlsec.Transform.ENVELOPED)
    xmlsec.template.add_transform(ref, xmlsec.Transform.EXCL_C14N)
    key_info = xmlsec.template.ensure_key_info(signature)
    xmlsec.template.add_x509_data(key_info)
    element.find(f"{{{NS_SAML}}}Issuer").addnext(signature)
    ctx = xmlsec.SignatureContext()
    key = xmlsec.Key.from_memory(keys.key_pem, xmlsec.KeyFormat.PEM)
    key.load_cert_from_memory(keys.cert_pem, xmlsec.KeyFormat.CERT_PEM)
    ctx.key = key
    ctx.sign(signature)


def assertion_of(root: etree._Element) -> etree._Element:
    """Return the (single) Assertion child of a Response tree."""
    return root.find(f"{{{NS_SAML}}}Assertion")


def build_signed(spec: ResponseSpec, keys: IdpKeys) -> etree._Element:
    """Build the Response tree for ``spec`` and sign it as ``spec.sign`` says."""
    root = build_response_xml(spec)
    sha1 = spec.sig_alg == "sha1"
    if spec.sign in ("assertion", "both"):
        sign_element(assertion_of(root), keys, sha1=sha1)
    if spec.sign in ("response", "both"):
        sign_element(root, keys, sha1=sha1)
    return root


def encode(root: etree._Element | bytes) -> str:
    """Base64-encode a Response tree/bytes exactly as a browser form post would."""
    raw = root if isinstance(root, bytes) else etree.tostring(root)
    return base64.b64encode(raw).decode()


def signed_response_b64(spec: ResponseSpec, keys: IdpKeys) -> str:
    """Build, sign and base64-encode a Response in one call."""
    return encode(build_signed(spec, keys))


def request_id_from_redirect(sso_url: str) -> str:
    """Extract the AuthnRequest ID from an IdP redirect URL's SAMLRequest."""
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(sso_url).query)
    raw = zlib.decompress(base64.b64decode(query["SAMLRequest"][0]), -15)
    return etree.fromstring(raw).get("ID")
