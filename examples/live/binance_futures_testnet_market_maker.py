#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2023 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------

from decimal import Decimal

from nautilus_trader.adapters.binance.common.enums import BinanceAccountType
from nautilus_trader.adapters.binance.config import BinanceDataClientConfig
from nautilus_trader.adapters.binance.config import BinanceExecClientConfig
from nautilus_trader.adapters.binance.factories import BinanceLiveDataClientFactory
from nautilus_trader.adapters.binance.factories import BinanceLiveExecClientFactory
from nautilus_trader.config import CacheDatabaseConfig
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.examples.strategies.volatility_market_maker import VolatilityMarketMaker
from nautilus_trader.examples.strategies.volatility_market_maker import VolatilityMarketMakerConfig
from nautilus_trader.live.node import TradingNode


# *** THIS IS A TEST STRATEGY WITH NO ALPHA ADVANTAGE WHATSOEVER. ***
# *** IT IS NOT INTENDED TO BE USED TO TRADE LIVE WITH REAL MONEY. ***

# *** THIS INTEGRATION IS STILL UNDER CONSTRUCTION. ***
# *** CONSIDER IT TO BE IN AN UNSTABLE BETA PHASE AND EXERCISE CAUTION. ***

# Configure the trading node
config_node = TradingNodeConfig(
    trader_id="TESTER-001",
    logging=LoggingConfig(
        log_level="INFO",
        # log_level_file="DEBUG",
        # log_file_format="json",
    ),
    exec_engine=LiveExecEngineConfig(
        reconciliation=True,
        reconciliation_lookback_mins=1440,
        filter_position_reports=True,
    ),
    cache_database=CacheDatabaseConfig(
        type="in-memory",
        timestamps_as_iso8601=True,
    ),
    data_clients={
        "BINANCE": BinanceDataClientConfig(
            api_key=None,  # "YOUR_BINANCE_TESTNET_API_KEY"
            api_secret=None,  # "YOUR_BINANCE_TESTNET_API_SECRET"
            account_type=BinanceAccountType.USDT_FUTURE,
            base_url_http=None,  # Override with custom endpoint
            base_url_ws=None,  # Override with custom endpoint
            us=False,  # If client is for Binance US
            testnet=True,  # If client uses the testnet
            instrument_provider=InstrumentProviderConfig(load_all=True),
        ),
    },
    exec_clients={
        "BINANCE": BinanceExecClientConfig(
            api_key=None,  # "YOUR_BINANCE_TESTNET_API_KEY"
            api_secret=None,  # "YOUR_BINANCE_TESTNET_API_SECRET"
            account_type=BinanceAccountType.USDT_FUTURE,
            base_url_http=None,  # Override with custom endpoint
            base_url_ws=None,  # Override with custom endpoint
            us=False,  # If client is for Binance US
            testnet=True,  # If client uses the testnet
            instrument_provider=InstrumentProviderConfig(load_all=True),
        ),
    },
    timeout_connection=20.0,
    timeout_reconciliation=10.0,
    timeout_portfolio=10.0,
    timeout_disconnection=10.0,
    timeout_post_stop=5.0,
)
# Instantiate the node with a configuration
node = TradingNode(config=config_node)

# Configure your strategy
strat_config = VolatilityMarketMakerConfig(
    instrument_id="ETHUSDT-PERP.BINANCE",
    external_order_claims=["ETHUSDT-PERP.BINANCE"],
    bar_type="ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL",
    atr_period=20,
    atr_multiple=6.0,
    trade_size=Decimal("0.010"),
)
# Instantiate your strategy
strategy = VolatilityMarketMaker(config=strat_config)

# Add your strategies and modules
node.trader.add_strategy(strategy)

# Register your client factories with the node (can take user defined factories)
node.add_data_client_factory("BINANCE", BinanceLiveDataClientFactory)
node.add_exec_client_factory("BINANCE", BinanceLiveExecClientFactory)
node.build()


# Stop and dispose of the node with SIGINT/CTRL+C
if __name__ == "__main__":
    try:
        node.run()
    finally:
        node.dispose()
