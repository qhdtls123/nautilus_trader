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

import asyncio
from typing import Optional, Union

import msgspec
import pandas as pd

from nautilus_trader.adapters.binance.common.constants import BINANCE_VENUE
from nautilus_trader.adapters.binance.common.enums import BinanceAccountType
from nautilus_trader.adapters.binance.common.enums import BinanceEnumParser
from nautilus_trader.adapters.binance.common.enums import BinanceKlineInterval
from nautilus_trader.adapters.binance.common.schemas.market import BinanceAggregatedTradeMsg
from nautilus_trader.adapters.binance.common.schemas.market import BinanceCandlestickMsg
from nautilus_trader.adapters.binance.common.schemas.market import BinanceDataMsgWrapper
from nautilus_trader.adapters.binance.common.schemas.market import BinanceOrderBookMsg
from nautilus_trader.adapters.binance.common.schemas.market import BinanceQuoteMsg
from nautilus_trader.adapters.binance.common.schemas.market import BinanceTickerMsg
from nautilus_trader.adapters.binance.common.schemas.symbol import BinanceSymbol
from nautilus_trader.adapters.binance.common.types import BinanceBar
from nautilus_trader.adapters.binance.common.types import BinanceTicker
from nautilus_trader.adapters.binance.config import BinanceDataClientConfig
from nautilus_trader.adapters.binance.http.client import BinanceHttpClient
from nautilus_trader.adapters.binance.http.market import BinanceMarketHttpAPI
from nautilus_trader.adapters.binance.websocket.client import BinanceWebSocketClient
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.clock import LiveClock
from nautilus_trader.common.enums import LogColor
from nautilus_trader.common.logging import Logger
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.core.correctness import PyCondition
from nautilus_trader.core.datetime import secs_to_millis
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.msgbus.bus import MessageBus


class BinanceCommonDataClient(LiveMarketDataClient):
    """
    Provides a data client of common methods for the `Binance` exchange.

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
        The event loop for the client.
    client : BinanceHttpClient
        The binance HTTP client.
    market : BinanceMarketHttpAPI
        The binance Market HTTP API.
    enum_parser : BinanceEnumParser
        The parser for Binance enums.
    msgbus : MessageBus
        The message bus for the client.
    cache : Cache
        The cache for the client.
    clock : LiveClock
        The clock for the client.
    logger : Logger
        The logger for the client.
    instrument_provider : InstrumentProvider
        The instrument provider.
    account_type : BinanceAccountType
        The account type for the client.
    base_url_ws : str
        The base URL for the WebSocket client.
    config : BinanceDataClientConfig
        The configuration for the client.

    Warnings
    --------
    This class should not be used directly, but through a concrete subclass.

    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        client: BinanceHttpClient,
        market: BinanceMarketHttpAPI,
        enum_parser: BinanceEnumParser,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        logger: Logger,
        instrument_provider: InstrumentProvider,
        account_type: BinanceAccountType,
        base_url_ws: str,
        config: BinanceDataClientConfig,
    ) -> None:
        super().__init__(
            loop=loop,
            client_id=ClientId(BINANCE_VENUE.value),
            venue=BINANCE_VENUE,
            instrument_provider=instrument_provider,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            logger=logger,
        )

        self._binance_account_type = account_type
        self._use_agg_trade_ticks = config.use_agg_trade_ticks
        self._log.info(f"Account type: {self._binance_account_type.value}.", LogColor.BLUE)
        self._log.info(f"{config.use_agg_trade_ticks=}", LogColor.BLUE)

        self._update_instrument_interval: int = 60 * 60  # Once per hour (hardcode)
        self._update_instruments_task: Optional[asyncio.Task] = None

        self._connect_websockets_delay: float = 0.0  # Delay for bulk subscriptions to come in
        self._connect_websockets_task: Optional[asyncio.Task] = None

        # HTTP API
        self._http_client = client
        self._http_market = market

        # Enum parser
        self._enum_parser = enum_parser

        # WebSocket API
        self._ws_client = BinanceWebSocketClient(
            clock=clock,
            logger=logger,
            handler=self._handle_ws_message,
            base_url=base_url_ws,
        )

        # Hot caches
        self._instrument_ids: dict[str, InstrumentId] = {}
        self._book_buffer: dict[
            InstrumentId,
            list[Union[OrderBookDelta, OrderBookDeltas]],
        ] = {}

        self._log.info(f"Base URL HTTP {self._http_client.base_url}.", LogColor.BLUE)
        self._log.info(f"Base URL WebSocket {base_url_ws}.", LogColor.BLUE)

        # Register common WebSocket message handlers
        self._ws_handlers = {
            "@bookTicker": self._handle_book_ticker,
            "@ticker": self._handle_ticker,
            "@kline": self._handle_kline,
            "@trade": self._handle_trade,
            "@aggTrade": self._handle_agg_trade,
            "@depth@": self._handle_book_diff_update,
            "@depth5": self._handle_book_partial_update,
            "@depth10": self._handle_book_partial_update,
            "@depth20": self._handle_book_partial_update,
        }

        # WebSocket msgspec decoders
        self._decoder_data_msg_wrapper = msgspec.json.Decoder(BinanceDataMsgWrapper)
        self._decoder_order_book_msg = msgspec.json.Decoder(BinanceOrderBookMsg)
        self._decoder_quote_msg = msgspec.json.Decoder(BinanceQuoteMsg)
        self._decoder_ticker_msg = msgspec.json.Decoder(BinanceTickerMsg)
        self._decoder_candlestick_msg = msgspec.json.Decoder(BinanceCandlestickMsg)
        self._decoder_agg_trade_msg = msgspec.json.Decoder(BinanceAggregatedTradeMsg)

    async def _connect(self) -> None:
        self._log.info("Initializing instruments...")
        await self._instrument_provider.initialize()

        self._send_all_instruments_to_data_engine()
        self._update_instruments_task = self.create_task(self._update_instruments())

    async def _update_instruments(self) -> None:
        try:
            while True:
                self._log.debug(
                    f"Scheduled `update_instruments` to run in "
                    f"{self._update_instrument_interval}s.",
                )
                await asyncio.sleep(self._update_instrument_interval)
                await self._instrument_provider.load_all_async()
                self._send_all_instruments_to_data_engine()
        except asyncio.CancelledError:
            self._log.debug("`update_instruments` task was canceled.")

    async def _disconnect(self) -> None:
        # Cancel update instruments task
        if self._update_instruments_task:
            self._log.debug("Canceling `update_instruments` task...")
            self._update_instruments_task.cancel()
            self._update_instruments_task = None

        await self._ws_client.disconnect()

    # -- SUBSCRIPTIONS ----------------------------------------------------------------------------

    async def _subscribe(self, data_type: DataType) -> None:
        # Replace method in child class, for exchange specific data types.
        raise NotImplementedError(f"Cannot subscribe to {data_type.type} (not implemented).")

    async def _subscribe_instruments(self) -> None:
        pass  # Do nothing further

    async def _subscribe_instrument(self, instrument_id: InstrumentId) -> None:
        pass  # Do nothing further

    async def _subscribe_order_book_deltas(
        self,
        instrument_id: InstrumentId,
        book_type: BookType,
        depth: Optional[int] = None,
        kwargs: Optional[dict] = None,
    ) -> None:
        update_speed = None
        if kwargs is not None:
            update_speed = kwargs.get("update_speed")
        await self._subscribe_order_book(
            instrument_id=instrument_id,
            book_type=book_type,
            update_speed=update_speed,
            depth=depth,
        )

    async def _subscribe_order_book_snapshots(
        self,
        instrument_id: InstrumentId,
        book_type: BookType,
        depth: Optional[int] = None,
        kwargs: Optional[dict] = None,
    ) -> None:
        update_speed = None
        if kwargs is not None:
            update_speed = kwargs.get("update_speed")
        await self._subscribe_order_book(
            instrument_id=instrument_id,
            book_type=book_type,
            update_speed=update_speed,
            depth=depth,
        )

    async def _subscribe_order_book(  # noqa (too complex)
        self,
        instrument_id: InstrumentId,
        book_type: BookType,
        update_speed: Optional[int] = None,
        depth: Optional[int] = None,
    ) -> None:
        if book_type == BookType.L3_MBO:
            self._log.error(
                "Cannot subscribe to order book deltas: "
                "L3_MBO data is not published by Binance. "
                "Valid book types are L1_TBBO, L2_MBP.",
            )
            return

        valid_speeds = [100, 1000]
        if self._binance_account_type.is_futures:
            if update_speed is None:
                update_speed = 0  # default 0 ms for futures.
            valid_speeds = [0, 100, 250, 500]  # 0ms option for futures exists but not documented?
        elif update_speed is None:
            update_speed = 100  # default 100ms for spot
        if update_speed not in valid_speeds:
            self._log.error(
                "Cannot subscribe to order book:"
                f"invalid `update_speed`, was {update_speed}. "
                f"Valid update speeds are {valid_speeds} ms.",
            )
            return

        if depth is None:
            depth = 0

        # Add delta stream buffer
        self._book_buffer[instrument_id] = []

        snapshot: Optional[OrderBookDeltas] = None
        if 0 < depth <= 20:
            if depth not in (5, 10, 20):
                self._log.error(
                    "Cannot subscribe to order book snapshots: "
                    f"invalid `depth`, was {depth}. "
                    "Valid depths are 5, 10 or 20.",
                )
                return
            await self._ws_client.subscribe_partial_book_depth(
                symbol=instrument_id.symbol.value,
                depth=depth,
                speed=update_speed,
            )

            snapshot = await self._http_market.request_order_book_snapshot(
                instrument_id=instrument_id,
                limit=depth,
                ts_init=self._clock.timestamp_ns(),
            )
            self._handle_data(snapshot)
        else:
            await self._ws_client.subscribe_diff_book_depth(
                symbol=instrument_id.symbol.value,
                speed=update_speed,
            )

        book_buffer = self._book_buffer.pop(instrument_id, [])
        for deltas in book_buffer:
            if snapshot and deltas.sequence <= snapshot.sequence:
                continue
            self._handle_data(deltas)

    async def _subscribe_ticker(self, instrument_id: InstrumentId) -> None:
        await self._ws_client.subscribe_ticker(instrument_id.symbol.value)

    async def _subscribe_quote_ticks(self, instrument_id: InstrumentId) -> None:
        await self._ws_client.subscribe_book_ticker(instrument_id.symbol.value)

    async def _subscribe_trade_ticks(self, instrument_id: InstrumentId) -> None:
        if self._use_agg_trade_ticks:
            await self._ws_client.subscribe_agg_trades(instrument_id.symbol.value)
        else:
            await self._ws_client.subscribe_trades(instrument_id.symbol.value)

    async def _subscribe_bars(self, bar_type: BarType) -> None:
        PyCondition.true(bar_type.is_externally_aggregated(), "aggregation_source is not EXTERNAL")

        if not bar_type.spec.is_time_aggregated():
            self._log.error(
                f"Cannot subscribe to {bar_type}: only time bars are aggregated by Binance.",
            )
            return

        resolution = self._enum_parser.parse_internal_bar_agg(bar_type.spec.aggregation)
        if self._binance_account_type.is_futures and resolution == "s":
            self._log.error(
                f"Cannot subscribe to {bar_type}. ",
                "Second interval bars are not aggregated by Binance Futures.",
            )
        try:
            interval = BinanceKlineInterval(f"{bar_type.spec.step}{resolution}")
        except ValueError:
            self._log.error(
                f"Bar interval {bar_type.spec.step}{resolution} not supported by Binance.",
            )
            return

        await self._ws_client.subscribe_bars(
            symbol=bar_type.instrument_id.symbol.value,
            interval=interval.value,
        )
        self._add_subscription_bars(bar_type)

    async def _unsubscribe(self, data_type: DataType) -> None:
        # Replace method in child class, for exchange specific data types.
        raise NotImplementedError(f"Cannot unsubscribe from {data_type.type} (not implemented).")

    async def _unsubscribe_instruments(self) -> None:
        pass  # Do nothing further

    async def _unsubscribe_instrument(self, instrument_id: InstrumentId) -> None:
        pass  # Do nothing further

    async def _unsubscribe_order_book_deltas(self, instrument_id: InstrumentId) -> None:
        pass  # TODO: Unsubscribe from Binance if no other subscriptions

    async def _unsubscribe_order_book_snapshots(self, instrument_id: InstrumentId) -> None:
        pass  # TODO: Unsubscribe from Binance if no other subscriptions

    async def _unsubscribe_ticker(self, instrument_id: InstrumentId) -> None:
        pass  # TODO: Unsubscribe from Binance if no other subscriptions

    async def _unsubscribe_quote_ticks(self, instrument_id: InstrumentId) -> None:
        pass  # TODO: Unsubscribe from Binance if no other subscriptions

    async def _unsubscribe_trade_ticks(self, instrument_id: InstrumentId) -> None:
        pass  # TODO: Unsubscribe from Binance if no other subscriptions

    async def _unsubscribe_bars(self, bar_type: BarType) -> None:
        pass  # TODO: Unsubscribe from Binance if no other subscriptions

    # -- REQUESTS ---------------------------------------------------------------------------------

    async def _request_instrument(self, instrument_id: InstrumentId, correlation_id: UUID4) -> None:
        instrument: Optional[Instrument] = self._instrument_provider.find(instrument_id)
        if instrument is None:
            self._log.error(f"Cannot find instrument for {instrument_id}.")
            return

        data_type = DataType(
            type=Instrument,
            metadata={"instrument_id": instrument_id},
        )

        self._handle_data_response(
            data_type=data_type,
            data=[instrument],  # Data engine handles lists of instruments
            correlation_id=correlation_id,
        )

    async def _request_quote_ticks(
        self,
        instrument_id: InstrumentId,
        limit: int,
        correlation_id: UUID4,
        start: Optional[pd.Timestamp] = None,
        end: Optional[pd.Timestamp] = None,
    ) -> None:
        self._log.error(
            "Cannot request historical quote ticks: not published by Binance.",
        )

    async def _request_trade_ticks(
        self,
        instrument_id: InstrumentId,
        limit: int,
        correlation_id: UUID4,
        start: Optional[pd.Timestamp] = None,
        end: Optional[pd.Timestamp] = None,
    ) -> None:
        if limit == 0 or limit > 1000:
            limit = 1000

        if not self._use_agg_trade_ticks:
            if start is not None or end is not None:
                self._log.warning(
                    "Trade ticks have been requested with a from/to time range, "
                    f"however the request will be for the most recent {limit}. "
                    "Consider using aggregated trade ticks (`use_agg_trade_ticks`).",
                )
            ticks = await self._http_market.request_trade_ticks(
                instrument_id=instrument_id,
                limit=limit,
                ts_init=self._clock.timestamp_ns(),
            )
        else:
            # Convert from timestamps to milliseconds
            start_time_ms = None
            end_time_ms = None
            if start:
                start_time_ms = int(start.timestamp() * 1000)
            if end:
                end_time_ms = int(end.timestamp() * 1000)
            ticks = await self._http_market.request_agg_trade_ticks(
                instrument_id=instrument_id,
                limit=limit,
                start_time=start_time_ms,
                end_time=end_time_ms,
                ts_init=self._clock.timestamp_ns(),
            )

        self._handle_trade_ticks(instrument_id, ticks, correlation_id)

    async def _request_bars(  # (too complex)
        self,
        bar_type: BarType,
        limit: int,
        correlation_id: UUID4,
        start: Optional[pd.Timestamp] = None,
        end: Optional[pd.Timestamp] = None,
    ) -> None:
        if limit == 0 or limit > 1000:
            limit = 1000

        if bar_type.is_internally_aggregated():
            self._log.error(
                f"Cannot request {bar_type}: "
                f"only historical bars with EXTERNAL aggregation available from Binance.",
            )
            return

        if not bar_type.spec.is_time_aggregated():
            self._log.error(
                f"Cannot request {bar_type}: only time bars are aggregated by Binance.",
            )
            return

        resolution = self._enum_parser.parse_internal_bar_agg(bar_type.spec.aggregation)
        if not self._binance_account_type.is_spot_or_margin and resolution == "s":
            self._log.error(
                f"Cannot request {bar_type}: ",
                "second interval bars are not aggregated by Binance Futures.",
            )
        try:
            interval = BinanceKlineInterval(f"{bar_type.spec.step}{resolution}")
        except ValueError:
            self._log.error(
                f"Cannot create Binance Kline interval. {bar_type.spec.step}{resolution} "
                "not supported.",
            )
            return

        if bar_type.spec.price_type != PriceType.LAST:
            self._log.error(
                f"Cannot request {bar_type}: "
                f"only historical bars for LAST price type available from Binance.",
            )
            return

        start_time_ms = None
        if start is not None:
            start_time_ms = secs_to_millis(start.timestamp())

        end_time_ms = None
        if end is not None:
            end_time_ms = secs_to_millis(end.timestamp())

        bars = await self._http_market.request_binance_bars(
            bar_type=bar_type,
            interval=interval,
            start_time=start_time_ms,
            end_time=end_time_ms,
            limit=limit,
            ts_init=self._clock.timestamp_ns(),
        )

        partial: BinanceBar = bars.pop()
        self._handle_bars(bar_type, bars, partial, correlation_id)

    def _send_all_instruments_to_data_engine(self) -> None:
        for instrument in self._instrument_provider.get_all().values():
            self._handle_data(instrument)

        for currency in self._instrument_provider.currencies().values():
            self._cache.add_currency(currency)

    def _get_cached_instrument_id(self, symbol: str) -> InstrumentId:
        # Parse instrument ID
        binance_symbol = BinanceSymbol(symbol)
        assert binance_symbol
        nautilus_symbol: str = binance_symbol.parse_as_nautilus(
            self._binance_account_type,
        )
        instrument_id: Optional[InstrumentId] = self._instrument_ids.get(nautilus_symbol)
        if not instrument_id:
            instrument_id = InstrumentId(Symbol(nautilus_symbol), BINANCE_VENUE)
            self._instrument_ids[nautilus_symbol] = instrument_id
        return instrument_id

    # -- WEBSOCKET HANDLERS ---------------------------------------------------------------------------------

    def _handle_ws_message(self, raw: bytes) -> None:
        # TODO(cs): Uncomment for development
        # self._log.info(str(raw), LogColor.CYAN)
        wrapper = self._decoder_data_msg_wrapper.decode(raw)
        try:
            handled = False
            for handler in self._ws_handlers:
                if handler in wrapper.stream:
                    self._ws_handlers[handler](raw)
                    handled = True
            if not handled:
                self._log.error(
                    f"Unrecognized websocket message type: {wrapper.stream}",
                )
        except Exception as e:
            self._log.error(f"Error handling websocket message, {e}")

    def _handle_book_diff_update(self, raw: bytes) -> None:
        msg = self._decoder_order_book_msg.decode(raw)
        instrument_id: InstrumentId = self._get_cached_instrument_id(msg.data.s)
        book_deltas: OrderBookDeltas = msg.data.parse_to_order_book_deltas(
            instrument_id=instrument_id,
            ts_init=self._clock.timestamp_ns(),
        )
        book_buffer: Optional[list[Union[OrderBookDelta, OrderBookDeltas]]] = self._book_buffer.get(
            instrument_id,
        )
        if book_buffer is not None:
            book_buffer.append(book_deltas)
        else:
            self._handle_data(book_deltas)

    def _handle_book_ticker(self, raw: bytes) -> None:
        msg = self._decoder_quote_msg.decode(raw)
        instrument_id: InstrumentId = self._get_cached_instrument_id(msg.data.s)
        quote_tick: QuoteTick = msg.data.parse_to_quote_tick(
            instrument_id=instrument_id,
            ts_init=self._clock.timestamp_ns(),
        )
        self._handle_data(quote_tick)

    def _handle_ticker(self, raw: bytes) -> None:
        msg = self._decoder_ticker_msg.decode(raw)
        instrument_id: InstrumentId = self._get_cached_instrument_id(msg.data.s)
        ticker: BinanceTicker = msg.data.parse_to_binance_ticker(
            instrument_id=instrument_id,
            ts_init=self._clock.timestamp_ns(),
        )
        self._handle_data(ticker)

    def _handle_kline(self, raw: bytes) -> None:
        msg = self._decoder_candlestick_msg.decode(raw)
        if not msg.data.k.x:
            return  # Not closed yet
        instrument_id = self._get_cached_instrument_id(msg.data.s)
        bar: BinanceBar = msg.data.k.parse_to_binance_bar(
            instrument_id=instrument_id,
            enum_parser=self._enum_parser,
            ts_init=self._clock.timestamp_ns(),
        )
        self._handle_data(bar)

    def _handle_book_partial_update(self, raw: bytes) -> None:
        raise NotImplementedError("Please implement book partial update handling in child class.")

    def _handle_trade(self, raw: bytes) -> None:
        raise NotImplementedError("Please implement trade handling in child class.")

    def _handle_agg_trade(self, raw: bytes) -> None:
        msg = self._decoder_agg_trade_msg.decode(raw)
        instrument_id: InstrumentId = self._get_cached_instrument_id(msg.data.s)
        trade_tick: TradeTick = msg.data.parse_to_trade_tick(
            instrument_id=instrument_id,
            ts_init=self._clock.timestamp_ns(),
        )
        self._handle_data(trade_tick)
