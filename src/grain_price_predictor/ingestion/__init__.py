from .banxico import BanxicoIngester
from .cme import CMEIngester
from .conagua import CONAGUAIngester
from .nasa_power import NASAPowerIngester
from .noaa import NOAAIngester
from .siap import SIAPIngester
from .sniim import SNIIMIngester
from .usda_nass import USDANASSIngester
from .world_bank import WorldBankIngester

__all__ = [
    "BanxicoIngester",
    "CMEIngester",
    "CONAGUAIngester",
    "NASAPowerIngester",
    "NOAAIngester",
    "SIAPIngester",
    "SNIIMIngester",
    "USDANASSIngester",
    "WorldBankIngester",
]
