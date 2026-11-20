//! Pure-Rust AWS Signature Version 4 request signer.

use std::collections::BTreeMap;

use chrono::{DateTime, Utc};
use hmac::{Hmac, Mac};
use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use sha2::{Digest, Sha256};

use crate::common::{http_host_header, CloudError, CloudResult};

type HmacSha256 = Hmac<Sha256>;

/// AWS credentials used for SigV4 request signing.
#[derive(Clone)]
pub struct AwsCredentials {
    pub access_key_id: String,
    pub secret_access_key: String,
    pub session_token: Option<String>,
}

impl std::fmt::Debug for AwsCredentials {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AwsCredentials")
            .field("access_key_id", &"[REDACTED]")
            .field(
                "has_secret_access_key",
                &(!self.secret_access_key.is_empty()),
            )
            .field("has_session_token", &self.session_token.is_some())
            .finish()
    }
}

/// Input parameters for signing an AWS HTTP request with SigV4.
#[derive(Debug, Clone)]
pub struct SigV4Input<'a> {
    pub method: &'a str,
    pub url: &'a url::Url,
    pub region: &'a str,
    pub service: &'a str,
    pub payload: &'a [u8],
    pub extra_headers: &'a [(&'a str, &'a str)],
    pub include_payload_hash_header: bool,
    pub timestamp: DateTime<Utc>,
}

/// Output of SigV4 signing, including headers and intermediate diagnostic artifacts.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SignedSigV4 {
    pub authorization: String,
    pub amz_date: String,
    pub date_stamp: String,
    pub payload_sha256: String,
    pub signed_headers: String,
    pub canonical_request: String,
    pub string_to_sign: String,
    pub signature: String,
    pub headers: BTreeMap<String, String>,
}

impl SignedSigV4 {
    /// Convert signed headers into a `reqwest::header::HeaderMap`.
    pub fn to_header_map(&self) -> CloudResult<HeaderMap> {
        let mut map = HeaderMap::new();
        for (k, v) in &self.headers {
            // `host` is set by reqwest from the URL itself; adding an explicit invalid/duplicate
            // host header is unnecessary unless needed, though reqwest handles standard headers cleanly.
            if k == "host" {
                continue;
            }
            let name = HeaderName::from_bytes(k.as_bytes())
                .map_err(|e| CloudError::Auth(format!("invalid signed header name {k:?}: {e}")))?;
            let value = HeaderValue::from_str(v).map_err(|e| {
                CloudError::Auth(format!("invalid signed header value for {k:?}: {e}"))
            })?;
            map.insert(name, value);
        }
        let auth_val = HeaderValue::from_str(&self.authorization)
            .map_err(|e| CloudError::Auth(format!("invalid Authorization header: {e}")))?;
        map.insert(reqwest::header::AUTHORIZATION, auth_val);
        Ok(map)
    }
}

/// Sign an HTTP request using AWS Signature Version 4.
pub fn sign_request(creds: &AwsCredentials, input: &SigV4Input<'_>) -> CloudResult<SignedSigV4> {
    if creds.access_key_id.trim().is_empty() || creds.secret_access_key.trim().is_empty() {
        return Err(CloudError::Auth(
            "AWS access_key_id and secret_access_key must be non-empty".into(),
        ));
    }

    let method = input.method.trim().to_ascii_uppercase();
    let amz_date = input.timestamp.format("%Y%m%dT%H%M%SZ").to_string();
    let date_stamp = input.timestamp.format("%Y%m%d").to_string();
    let payload_sha256 = sha256_hex(input.payload);
    let host_header = http_host_header(input.url)?;

    let double_encode_path = input.service != "s3";
    let canonical_uri = canonicalize_uri_path(input.url.path(), double_encode_path);
    let canonical_query = canonicalize_query_string(input.url.query().unwrap_or(""));

    let mut headers_map: BTreeMap<String, String> = BTreeMap::new();
    headers_map.insert("host".to_string(), normalize_header_value(&host_header));
    headers_map.insert("x-amz-date".to_string(), amz_date.clone());

    if let Some(token) = creds
        .session_token
        .as_deref()
        .map(str::trim)
        .filter(|t| !t.is_empty())
    {
        headers_map.insert(
            "x-amz-security-token".to_string(),
            normalize_header_value(token),
        );
    }

    if input.include_payload_hash_header {
        headers_map.insert("x-amz-content-sha256".to_string(), payload_sha256.clone());
    }

    for (name, value) in input.extra_headers {
        let key = name.trim().to_ascii_lowercase();
        if !key.is_empty() {
            headers_map.insert(key, normalize_header_value(value));
        }
    }

    let mut canonical_headers = String::new();
    let mut signed_header_names = Vec::with_capacity(headers_map.len());
    for (k, v) in &headers_map {
        canonical_headers.push_str(k);
        canonical_headers.push(':');
        canonical_headers.push_str(v);
        canonical_headers.push('\n');
        signed_header_names.push(k.as_str());
    }
    let signed_headers = signed_header_names.join(";");

    let canonical_request = format!(
        "{method}\n{canonical_uri}\n{canonical_query}\n{canonical_headers}\n{signed_headers}\n{payload_sha256}"
    );
    let canonical_request_hash = sha256_hex(canonical_request.as_bytes());
    let credential_scope = format!(
        "{date_stamp}/{}/{}/aws4_request",
        input.region, input.service
    );
    let string_to_sign =
        format!("AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n{canonical_request_hash}");

    let signing_key = derive_signing_key(
        &creds.secret_access_key,
        &date_stamp,
        input.region,
        input.service,
    )?;
    let sig_bytes = hmac_sha256(&signing_key, string_to_sign.as_bytes())?;
    let signature = hex::encode(sig_bytes);

    let authorization = format!(
        "AWS4-HMAC-SHA256 Credential={}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}",
        creds.access_key_id.trim()
    );

    Ok(SignedSigV4 {
        authorization,
        amz_date,
        date_stamp,
        payload_sha256,
        signed_headers,
        canonical_request,
        string_to_sign,
        signature,
        headers: headers_map,
    })
}

/// Derive the SigV4 signing key (`kSigning`).
pub fn derive_signing_key(
    secret_access_key: &str,
    date_stamp: &str,
    region: &str,
    service: &str,
) -> CloudResult<[u8; 32]> {
    let initial = format!("AWS4{secret_access_key}");
    let k_date = hmac_sha256(initial.as_bytes(), date_stamp.as_bytes())?;
    let k_region = hmac_sha256(&k_date, region.as_bytes())?;
    let k_service = hmac_sha256(&k_region, service.as_bytes())?;
    hmac_sha256(&k_service, b"aws4_request")
}

/// Compute lowercase hex SHA-256 digest.
pub fn sha256_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    hex::encode(hasher.finalize())
}

/// Compute HMAC-SHA256.
pub fn hmac_sha256(key: &[u8], data: &[u8]) -> CloudResult<[u8; 32]> {
    let mut mac = HmacSha256::new_from_slice(key)
        .map_err(|e| CloudError::Auth(format!("HMAC key initialization failed: {e}")))?;
    mac.update(data);
    let bytes = mac.finalize().into_bytes();
    let mut out = [0u8; 32];
    out.copy_from_slice(&bytes);
    Ok(out)
}

/// Percent-encode a string following RFC 3986 / AWS SigV4 rules (uppercase hex).
pub fn aws_uri_encode(bytes: &[u8], encode_slash: bool) -> String {
    let mut out = String::with_capacity(bytes.len());
    for &b in bytes {
        let is_unreserved = b.is_ascii_alphanumeric()
            || matches!(b, b'-' | b'_' | b'.' | b'~')
            || (!encode_slash && b == b'/');
        if is_unreserved {
            out.push(char::from(b));
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

fn canonicalize_uri_path(raw_path: &str, double_encode: bool) -> String {
    if raw_path.is_empty() || raw_path == "/" {
        return "/".to_string();
    }

    let trailing_slash = raw_path.ends_with('/');
    let mut segments: Vec<&str> = Vec::new();
    for seg in raw_path.split('/') {
        match seg {
            "" | "." => {}
            ".." => {
                segments.pop();
            }
            other => segments.push(other),
        }
    }

    if segments.is_empty() {
        return "/".to_string();
    }

    let mut normalized = String::new();
    for seg in segments {
        normalized.push('/');
        if double_encode {
            normalized.push_str(&aws_uri_encode(seg.as_bytes(), true));
        } else {
            normalized.push_str(seg);
        }
    }
    if trailing_slash && !normalized.ends_with('/') {
        normalized.push('/');
    }
    normalized
}

fn canonicalize_query_string(raw_query: &str) -> String {
    if raw_query.is_empty() {
        return String::new();
    }

    let mut pairs: Vec<(String, String)> = raw_query
        .split('&')
        .filter(|part| !part.is_empty())
        .map(|part| {
            let (raw_k, raw_v) = match part.split_once('=') {
                Some((k, v)) => (k, v),
                None => (part, ""),
            };
            let decoded_k = percent_decode_bytes(raw_k);
            let decoded_v = percent_decode_bytes(raw_v);
            (
                aws_uri_encode(&decoded_k, true),
                aws_uri_encode(&decoded_v, true),
            )
        })
        .collect();

    pairs.sort();
    pairs
        .into_iter()
        .map(|(k, v)| format!("{k}={v}"))
        .collect::<Vec<_>>()
        .join("&")
}

fn percent_decode_bytes(input: &str) -> Vec<u8> {
    let bytes = input.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let (Some(hi), Some(lo)) = (hex_nibble(bytes[i + 1]), hex_nibble(bytes[i + 2])) {
                out.push((hi << 4) | lo);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    out
}

fn hex_nibble(b: u8) -> Option<u8> {
    match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        b'A'..=b'F' => Some(b - b'A' + 10),
        _ => None,
    }
}

fn normalize_header_value(raw: &str) -> String {
    let trimmed = raw.trim();
    let mut out = String::with_capacity(trimmed.len());
    let mut prev_space = false;
    for ch in trimmed.chars() {
        if ch.is_whitespace() {
            if !prev_space {
                out.push(' ');
                prev_space = true;
            }
        } else {
            out.push(ch);
            prev_space = false;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    #[test]
    fn sigv4_matches_known_iam_canonical_request_and_signature() {
        let creds = AwsCredentials {
            access_key_id: "AKIDEXAMPLE".to_string(),
            secret_access_key: "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY".to_string(),
            session_token: None,
        };
        let url = url::Url::parse("https://iam.amazonaws.com/?Action=ListUsers&Version=2010-05-08")
            .expect("valid url");
        let ts = Utc
            .with_ymd_and_hms(2015, 8, 30, 12, 36, 0)
            .single()
            .expect("valid ts");

        let signed = sign_request(
            &creds,
            &SigV4Input {
                method: "GET",
                url: &url,
                region: "us-east-1",
                service: "iam",
                payload: b"",
                extra_headers: &[],
                include_payload_hash_header: false,
                timestamp: ts,
            },
        )
        .expect("signing succeeds");

        let expected_cr = "GET\n/\nAction=ListUsers&Version=2010-05-08\nhost:iam.amazonaws.com\nx-amz-date:20150830T123600Z\n\nhost;x-amz-date\ne3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
        let expected_sts = "AWS4-HMAC-SHA256\n20150830T123600Z\n20150830/us-east-1/iam/aws4_request\n5599feeca6d065c7c80025038896f3f7f008849eacf307aa7d0cf8be7116cea6";
        let expected_sig = "b2e4af44cfad96d9ffa3c5653674a927b9b0995c33de22e1f843745ce37c1d5e";

        assert_eq!(signed.canonical_request, expected_cr);
        assert_eq!(signed.string_to_sign, expected_sts);
        assert_eq!(signed.signature, expected_sig);
        assert_eq!(
            signed.authorization,
            format!(
                "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/iam/aws4_request, SignedHeaders=host;x-amz-date, Signature={expected_sig}"
            )
        );
    }

    #[test]
    fn sigv4_handles_session_token_query_sorting_and_path_encoding() {
        let creds = AwsCredentials {
            access_key_id: "AKIDEXAMPLE".to_string(),
            secret_access_key: "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY".to_string(),
            session_token: Some("TOK/123+abc=".to_string()),
        };
        let url = url::Url::parse(
            "https://ec2.us-west-2.amazonaws.com/v1/my%20path?ZKey=hello%20world&Action=DescribeInstances&Empty=&Version=2016-11-15",
        )
        .expect("valid url");
        let ts = Utc
            .with_ymd_and_hms(2026, 11, 20, 14, 0, 0)
            .single()
            .expect("valid ts");

        let signed = sign_request(
            &creds,
            &SigV4Input {
                method: "GET",
                url: &url,
                region: "us-west-2",
                service: "ec2",
                payload: b"",
                extra_headers: &[],
                include_payload_hash_header: false,
                timestamp: ts,
            },
        )
        .expect("signing succeeds");

        let expected_cr = "GET\n/v1/my%2520path\nAction=DescribeInstances&Empty=&Version=2016-11-15&ZKey=hello%20world\nhost:ec2.us-west-2.amazonaws.com\nx-amz-date:20261120T140000Z\nx-amz-security-token:TOK/123+abc=\n\nhost;x-amz-date;x-amz-security-token\ne3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
        let expected_sig = "53f6d2fc16ac8b25730cee2422c06c18ceb02217e322e7c6558df11f7986f9a4";

        assert_eq!(signed.canonical_request, expected_cr);
        assert_eq!(signed.signature, expected_sig);
    }

    #[test]
    fn sigv4_s3_includes_payload_hash_and_single_encodes_path() {
        let creds = AwsCredentials {
            access_key_id: "AKIDEXAMPLE".to_string(),
            secret_access_key: "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY".to_string(),
            session_token: None,
        };
        let url = url::Url::parse("https://s3.us-east-1.amazonaws.com/").expect("valid url");
        let ts = Utc
            .with_ymd_and_hms(2026, 11, 20, 14, 0, 0)
            .single()
            .expect("valid ts");

        let signed = sign_request(
            &creds,
            &SigV4Input {
                method: "GET",
                url: &url,
                region: "us-east-1",
                service: "s3",
                payload: b"",
                extra_headers: &[],
                include_payload_hash_header: true,
                timestamp: ts,
            },
        )
        .expect("signing succeeds");

        let expected_cr = "GET\n/\n\nhost:s3.us-east-1.amazonaws.com\nx-amz-content-sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\nx-amz-date:20261120T140000Z\n\nhost;x-amz-content-sha256;x-amz-date\ne3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
        let expected_sig = "2bb1ca4f031d010b3b3656770b411c5138dbc6f9d2a0cf4ceaf632a0dace8fe2";

        assert_eq!(signed.canonical_request, expected_cr);
        assert_eq!(signed.signature, expected_sig);
    }

    #[test]
    fn sigv4_debug_masks_secret_key() {
        let creds = AwsCredentials {
            access_key_id: "AKIA_SECRET_KEY_ID".to_string(),
            secret_access_key: "super-secret-value".to_string(),
            session_token: Some("session-secret".to_string()),
        };
        let dbg = format!("{creds:?}");
        assert!(!dbg.contains("AKIA_SECRET_KEY_ID"));
        assert!(!dbg.contains("super-secret-value"));
        assert!(!dbg.contains("session-secret"));
    }

    #[test]
    fn sigv4_rejects_empty_credentials() {
        let creds = AwsCredentials {
            access_key_id: "".to_string(),
            secret_access_key: "secret".to_string(),
            session_token: None,
        };
        let url = url::Url::parse("https://ec2.us-east-1.amazonaws.com/").expect("valid url");
        let err = sign_request(
            &creds,
            &SigV4Input {
                method: "GET",
                url: &url,
                region: "us-east-1",
                service: "ec2",
                payload: b"",
                extra_headers: &[],
                include_payload_hash_header: false,
                timestamp: Utc::now(),
            },
        )
        .expect_err("empty access key must fail");
        assert!(matches!(err, CloudError::Auth(_)));
    }
}
