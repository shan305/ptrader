from fastapi import APIRouter

from . import accounts, market_data, orders, positions, symbols

router = APIRouter(prefix="/api/v1")
router.include_router(accounts.router)
router.include_router(symbols.router)
router.include_router(market_data.router)
router.include_router(orders.router)
router.include_router(positions.router)
