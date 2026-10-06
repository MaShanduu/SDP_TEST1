"""Author identity endpoints: listing, merging, detaching, rename, suggestions."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import authors as authors_svc
from ..models import AuthorPatchIn, MergeIn
from .deps import db_conn, load_repo

router = APIRouter(prefix="/repos/{repo_id}/authors", tags=["authors"])


@router.get("")
def list_authors(repo_id: int, conn=Depends(db_conn)) -> list[dict]:
    """Effective authors with merged identities and pre-mailmap raw variants."""
    load_repo(repo_id, conn)
    return authors_svc.list_authors(conn, repo_id)


@router.get("/suggestions")
def get_suggestions(repo_id: int, conn=Depends(db_conn)) -> list[dict]:
    """Heuristic merge suggestions: shared emails and near-identical names."""
    load_repo(repo_id, conn)
    return authors_svc.suggestions(conn, repo_id)


@router.post("/merge")
def merge_authors(repo_id: int, body: MergeIn, conn=Depends(db_conn)) -> dict:
    """Merge identities into one effective author (metrics update immediately)."""
    load_repo(repo_id, conn)
    return authors_svc.merge_authors(conn, repo_id, body.target_id, body.source_ids)


@router.post("/{identity_id}/detach")
def detach_identity(repo_id: int, identity_id: int, conn=Depends(db_conn)) -> dict:
    """Un-merge an identity back into a standalone author."""
    load_repo(repo_id, conn)
    return authors_svc.detach_identity(conn, repo_id, identity_id)


@router.patch("/{author_id}")
def rename_author(repo_id: int, author_id: int, body: AuthorPatchIn, conn=Depends(db_conn)) -> dict:
    """Set a custom display name/email on an effective author."""
    load_repo(repo_id, conn)
    return authors_svc.rename_author(conn, repo_id, author_id, body.name, body.email)
