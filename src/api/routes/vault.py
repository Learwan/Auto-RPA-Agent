from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.config import settings
from src.vault.manager import Vault

router = APIRouter()

_vault = Vault(vault_key=getattr(settings, "VAULT_KEY", ""))


@router.post("/credentials")
async def store_credential(name: str, value: str, credential_type: str = "password", description: str = ""):
    try:
        cred = _vault.store(name, value, credential_type, description)
        return {"credential_id": cred.credential_id, "name": cred.name, "type": cred.credential_type}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/credentials")
async def list_credentials():
    return _vault.list_credentials()


@router.get("/credentials/{credential_id}")
async def get_credential(credential_id: str):
    value = _vault.retrieve(credential_id)
    if value is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    return {"credential_id": credential_id, "value": value}


@router.get("/credentials/by-name/{name}")
async def get_credential_by_name(name: str):
    value = _vault.retrieve_by_name(name)
    if value is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    return {"name": name, "value": value}


@router.delete("/credentials/{credential_id}")
async def delete_credential(credential_id: str):
    if _vault.delete(credential_id):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Credential not found")
