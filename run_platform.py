"""Trading Platform Runner script.
"""

import sys
import uvicorn
from libs.config.settings import get_platform_settings

if __name__ == "__main__":
    settings = get_platform_settings()
    print("=" * 60)
    print("Starting ICICI Direct Options Trading Platform v2...")
    print(f"Environment:         {settings.app_env.value.upper()}")
    print(f"Trading Mode:        {settings.default_trading_mode.value}")
    print(f"Backend API Gateway: http://{settings.api_host}:{settings.api_port}")
    print(f"API Documentation:   http://{settings.api_host}:{settings.api_port}/docs")
    print(f"WebSocket Stream:    ws://{settings.api_host}:{settings.api_port}/ws/live")
    print(f"Event Bus:           {settings.event_bus_backend.value}")
    print(f"Market Backend:      {settings.market_data_backend.value}")
    print("=" * 60)
    uvicorn.run(
        "services.api_gateway.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
        log_level=settings.log_level.lower(),
    )

