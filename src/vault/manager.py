from __future__ import annotations

import base64
import hashlib
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

_VAULT_KEY_ENV = "AUTO_AGENT_VAULT_KEY"
_VAULT_SALT = b"auto_agent_vault_salt_v1"


@dataclass
class Credential:
    credential_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    credential_type: str = "password"
    encrypted_value: bytes = b""
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    tags: list[str] = field(default_factory=list)


class Vault:
    def __init__(self, vault_key: str | None = None):
        self._key = vault_key or os.environ.get(_VAULT_KEY_ENV, "")
        self._credentials: dict[str, Credential] = {}

    def _get_encryption_key(self) -> bytes:
        if not self._key:
            raise ValueError("Vault key not configured. Set AUTO_AGENT_VAULT_KEY environment variable.")
        return hashlib.pbkdf2_hmac("sha256", self._key.encode(), _VAULT_SALT, 100000)

    def _encrypt(self, plaintext: str) -> bytes:
        key = self._get_encryption_key()
        encoded = plaintext.encode("utf-8")
        xored = bytes(
            a ^ b for a, b in zip(encoded, (key * (len(encoded) // len(key) + 1))[: len(encoded)], strict=False)
        )
        return base64.b64encode(xored)

    def _decrypt(self, encrypted: bytes) -> str:
        key = self._get_encryption_key()
        decoded = base64.b64decode(encrypted)
        xored = bytes(
            a ^ b for a, b in zip(decoded, (key * (len(decoded) // len(key) + 1))[: len(decoded)], strict=False)
        )
        return xored.decode("utf-8")

    def store(
        self,
        name: str,
        value: str,
        credential_type: str = "password",
        description: str = "",
        tags: list[str] | None = None,
    ) -> Credential:
        encrypted = self._encrypt(value)
        cred = Credential(
            name=name,
            credential_type=credential_type,
            encrypted_value=encrypted,
            description=description,
            tags=tags or [],
        )
        self._credentials[cred.credential_id] = cred
        logger.info("Stored credential: %s (type=%s)", name, credential_type)
        return cred

    def retrieve(self, credential_id: str) -> str | None:
        cred = self._credentials.get(credential_id)
        if cred is None:
            return None
        try:
            return self._decrypt(cred.encrypted_value)
        except Exception as e:
            logger.error("Failed to decrypt credential %s: %s", credential_id, e)
            return None

    def retrieve_by_name(self, name: str) -> str | None:
        for cred in self._credentials.values():
            if cred.name == name:
                return self.retrieve(cred.credential_id)
        return None

    def delete(self, credential_id: str) -> bool:
        if credential_id in self._credentials:
            del self._credentials[credential_id]
            logger.info("Deleted credential: %s", credential_id)
            return True
        return False

    def list_credentials(self) -> list[dict]:
        return [
            {
                "credential_id": c.credential_id,
                "name": c.name,
                "type": c.credential_type,
                "description": c.description,
                "created_at": c.created_at,
                "tags": c.tags,
            }
            for c in self._credentials.values()
        ]
