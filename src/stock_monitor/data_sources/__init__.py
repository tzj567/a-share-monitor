"""Licensed and public data-source adapters."""

from .akshare_source import AkShareProvider
from .cls_source import CLSAuthorizedNewsProvider
from .ifind_source import IFindProvider
from .fund_flow_source import AkShareFundFlowProvider
from .tushare_source import TuShareProvider

__all__ = ["AkShareFundFlowProvider", "AkShareProvider", "CLSAuthorizedNewsProvider", "IFindProvider", "TuShareProvider"]
