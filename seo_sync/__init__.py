"""Shared SEO data clients.

Deliberately has no knowledge of any particular site or business. Callers supply
credentials, property identifiers and a database connection; this package supplies
auth, pagination, retry and a stable row shape.

Installed per project and pinned per project:

    pip install "seo-sync @ git+https://github.com/exw2004/seo-sync@v0.7.0"

Each project runs its own copy against its own database. Nothing here is shared
at runtime between projects.
"""

__version__ = "0.7.0"
