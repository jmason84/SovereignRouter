"""Sensitivity scan: known credential formats are flagged; hashes,
UUIDs, container IDs and SHAs are explicitly NOT flagged (spec)."""

from __future__ import annotations

from sensitivity import scan_messages, scan_text

# ----------------------------------------------------------------------
# real credential formats -> sensitive
# ----------------------------------------------------------------------
def test_openai_style_key():
    assert scan_text("my key is sk-abc123def456ghi789jkl012").sensitive


def test_groq_key():
    assert scan_text("gsk_abcdefghijklmnopqrstuvwxyz0123456789").sensitive


def test_aws_access_key():
    assert scan_text("aws key AKIAIOSFODNN7EXAMPLE here").sensitive


def test_pem_block():
    assert scan_text("-----BEGIN RSA PRIVATE KEY-----\nMII...").sensitive
    assert scan_text("-----BEGIN PRIVATE KEY-----").sensitive


def test_bearer_token():
    assert scan_text("Authorization: Bearer abcdef1234567890ABCDEF").sensitive


def test_password_assignment():
    assert scan_text("password=Tr0ub4dor&3-correct-horse").sensitive
    assert scan_text('db_secret: "x9k2-long-secret-value"').sensitive


def test_github_and_slack_tokens():
    assert scan_text("ghp_abcdefghijklmnopqrstuvwxyz0123456789").sensitive
    assert scan_text("xoxb-123456789012-abcdefghijkl").sensitive


def test_connection_string_with_password():
    assert scan_text("postgres://user:s3cretpass@db.internal:5432/app").sensitive


def test_detector_names_only_no_match_text():
    r = scan_text("password=Tr0ub4dor&3-correct-horse")
    assert r.sensitive
    assert "Tr0ub4dor" not in " ".join(r.detectors)


# ----------------------------------------------------------------------
# identifiers that must NOT be flagged
# ----------------------------------------------------------------------
def test_uuid_not_flagged():
    assert not scan_text("request id 3f2b8c1e-1234-4a5b-8c9d-0123456789ab done").sensitive


def test_sha256_not_flagged():
    sha = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
    assert not scan_text(f"commit {sha} deployed").sensitive


def test_container_id_not_flagged():
    assert not scan_text("container a1b2c3d4e5f6 restarted ok").sensitive
    assert not scan_text("image sha256:2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e1fca7e58").sensitive


def test_hash_assignment_value_not_flagged():
    # an assignment whose value is a hash/UUID is an identifier, not a secret
    assert not scan_text("password=3f2b8c1e-1234-4a5b-8c9d-0123456789ab").sensitive


def test_placeholders_not_flagged():
    assert not scan_text("password=changeme").sensitive
    assert not scan_text("api_key=<your-key-here>").sensitive


def test_plain_infra_text_not_flagged():
    text = ("restart the docker container on proxmox host 192.168.0.166 "
            "and check the nginx logs")
    assert not scan_text(text).sensitive


def test_scan_messages_multimodal():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "token is gsk_abcdefghijklmnopqrstuvwxyz0123"}]}]
    assert scan_messages(msgs).sensitive
    msgs2 = [{"role": "user", "content": "just a normal question"}]
    assert not scan_messages(msgs2).sensitive
