"""Real PostgreSQL authority core. Copyright 2026 Remaker Digital. See NOTICE."""
__version__ = '0.1.0'
from gtkb_protocol import ProtocolError
from .settings import AuthorityOperation, OperationProvider, ParametersProvider, PostgresSettings
from .kernel import PostgresKernel, PostgresTransaction, PostgresKernelError
from .domain import AuthorityService, HistoricalCommitValidator
from .api import create_authority_app, NativeRoutes
__all__ = ['__version__','ProtocolError','AuthorityOperation','OperationProvider','ParametersProvider','PostgresSettings','PostgresKernel','PostgresTransaction','PostgresKernelError','AuthorityService','HistoricalCommitValidator','NativeRoutes','create_authority_app']
