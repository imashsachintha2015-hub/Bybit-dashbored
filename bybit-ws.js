/**
 * Bybit V5 Public WebSocket Client
 * Handles connection lifecycle, 20-second ping/pong heartbeats, latency measurement,
 * topic subscription, per-symbol orderbook snapshot + delta maintenance, and event dispatch.
 *
 * Every watched symbol gets a FULL deep feed (ticker + orderbook + trades + klines) all the
 * time — not just the one "focused" symbol shown in the chart. That's what lets the
 * intelligence engine run in parallel across the whole watchlist instead of just whichever
 * coin happens to be on screen. "Focus" is purely a display concern (which symbol's chart/
 * tape is currently rendered); it never changes what's subscribed.
 */

/** Parses a fetch Response as JSON, but turns a non-JSON body (most often an
 * HTML block/compliance page returned with a 200 status when Bybit's public
 * API is geo-restricted for the caller's region, or a proxy/CDN error page)
 * into a clear diagnostic instead of the default
 * "JSON.parse: unexpected character at line 1 column 1 of the JSON data". */
async function parseBybitJson(res, label) {
  const text = await res.text();
  if (!text) throw new Error(`${label}: empty response (status ${res.status})`);
  try {
    return JSON.parse(text);
  } catch (e) {
    const preview = text.slice(0, 100).replace(/\s+/g, ' ');
    const looksBlocked = /<html|<!doctype/i.test(preview);
    throw new Error(`${label}: non-JSON response (status ${res.status})${looksBlocked ? ' -- looks like an HTML page, likely a geo-block or CDN error page rather than the Bybit API' : ''}: "${preview}"`);
  }
}

class BybitWebSocketClient {
  constructor(options = {}) {
    this.category = options.category || 'linear'; // 'linear' | 'spot'
    this.network = options.network || 'mainnet';   // 'mainnet' | 'testnet'
    this.symbol = options.symbol || 'BTCUSDT';     // the symbol currently shown in the chart/tape
    // The chart's interval, which the user can change freely. It is a DISPLAY
    // concern only and is deliberately decoupled from what the engine analyses.
    this.klineInterval = options.klineInterval || '15';

    // The intervals the decision engine needs, always subscribed regardless of
    // what the chart is showing. V2 analysed whatever the chart happened to be
    // set to, on one timeframe — so changing the chart silently changed the
    // trading logic, and the logic only ever saw a single resolution.
    this.analysisIntervals = options.analysisIntervals || ['5', '15', '60'];

    // Every symbol that should get a full deep feed (order book, trades, klines, ticker).
    // The focused symbol is always expected to be a member of this list.
    this.watchSymbols = (options.watchSymbols || [this.symbol]).map(s => s.toUpperCase());

    this.ws = null;
    this.isConnected = false;
    this.isManualClose = false;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 10;
    this.reconnectTimer = null;

    // Heartbeat ping tracking
    this.pingInterval = null;
    this.pingSentTime = 0;
    this.latency = null;

    // Event callbacks — ticker/orderbook/trade/kline all fire for EVERY watched symbol,
    // each payload carries { symbol, data, ... } so consumers route by symbol themselves.
    this.listeners = {
      status: [],
      ticker: [],
      orderbook: [],
      trade: [],
      kline: [],
      historicalKlines: [],
      olderHistoricalKlines: [],
      packet: []
    };
    this.isLoadingOlder = false;

    // Tracks which topic strings are actually subscribed on the current connection.
    // Bybit rejects an ENTIRE subscribe batch if even one topic in it is already
    // subscribed, so every subscribe/unsubscribe must go through sendSubscribe/sendUnsubscribe.
    this.subscribedTopics = new Set();

    // Per-symbol local orderbook state (price -> size maps), one per watched symbol.
    this.orderbooks = {};
    this.watchSymbols.forEach(s => {
      this.orderbooks[s] = { bids: new Map(), asks: new Map(), lastUpdateId: 0 };
    });
  }

  getEndpoint() {
    const isTestnet = this.network === 'testnet';
    const base = isTestnet ? 'wss://stream-testnet.bybit.com/v5/public' : 'wss://stream.bybit.com/v5/public';
    return `${base}/${this.category}`;
  }

  on(event, callback) {
    if (this.listeners[event]) {
      this.listeners[event].push(callback);
    }
  }

  emit(event, data) {
    if (this.listeners[event]) {
      this.listeners[event].forEach(cb => {
        try {
          cb(data);
        } catch (err) {
          console.error(`Error in event listener for ${event}:`, err);
        }
      });
    }
  }

  connect() {
    this.isManualClose = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    const url = this.getEndpoint();
    this.emit('status', { status: 'connecting', url, latency: this.latency });

    try {
      this.ws = new WebSocket(url);
    } catch (e) {
      console.error('Failed to create WebSocket:', e);
      this.handleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.isConnected = true;
      this.reconnectAttempts = 0;
      // A fresh connection has no server-side subscriptions yet, regardless of what
      // was tracked before a reconnect.
      this.subscribedTopics.clear();
      this.emit('status', { status: 'connected', url, latency: this.latency });

      // Fetch historical candles for the focused symbol's chart
      this.loadHistoricalKlines();

      // Start 20-second heartbeat ping loop
      this.startHeartbeat();

      // Subscribe every watched symbol's full deep feed at once
      this.subscribeAllWatchFeeds();
    };

    this.ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        this.handleMessage(message);
      } catch (err) {
        console.error('Error parsing WebSocket message:', err, event.data);
      }
    };

    this.ws.onerror = (err) => {
      console.warn('WebSocket error:', err);
      this.emit('status', { status: 'error', error: err });
    };

    this.ws.onclose = (event) => {
      this.isConnected = false;
      this.stopHeartbeat();
      this.emit('status', { status: 'disconnected', code: event.code, reason: event.reason });

      if (!this.isManualClose) {
        this.handleReconnect();
      }
    };
  }

  startHeartbeat() {
    this.stopHeartbeat();
    // Bybit docs recommend sending ping every 20 seconds
    this.pingInterval = setInterval(() => {
      if (this.isConnected && this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.pingSentTime = performance.now();
        const pingMsg = {
          req_id: `ping_${Date.now()}`,
          op: 'ping'
        };
        this.ws.send(JSON.stringify(pingMsg));
      }
    }, 20000);

    // Also send an initial immediate ping to calculate latency
    setTimeout(() => {
      if (this.isConnected && this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.pingSentTime = performance.now();
        this.ws.send(JSON.stringify({ req_id: `init_ping_${Date.now()}`, op: 'ping' }));
      }
    }, 500);
  }

  stopHeartbeat() {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }

  handleReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.warn('Max reconnect attempts reached.');
      return;
    }

    const delay = Math.min(1000 * Math.pow(1.5, this.reconnectAttempts), 10000);
    this.reconnectAttempts++;
    console.log(`Reconnecting to Bybit WebSocket in ${Math.round(delay)}ms (attempt ${this.reconnectAttempts})...`);

    this.emit('status', { status: 'reconnecting', attempt: this.reconnectAttempts, delay });

    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, delay);
  }

  /** Subscribes only the topics not already active — Bybit rejects the whole batch otherwise. */
  sendSubscribe(topics) {
    if (!this.isConnected || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const newTopics = topics.filter(t => !this.subscribedTopics.has(t));
    if (!newTopics.length) return;
    newTopics.forEach(t => this.subscribedTopics.add(t));
    this.ws.send(JSON.stringify({ req_id: `sub_${Date.now()}`, op: 'subscribe', args: newTopics }));
  }

  /** Unsubscribes only topics we actually believe are active. */
  sendUnsubscribe(topics) {
    if (!this.isConnected || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const activeTopics = topics.filter(t => this.subscribedTopics.has(t));
    if (!activeTopics.length) return;
    activeTopics.forEach(t => this.subscribedTopics.delete(t));
    this.ws.send(JSON.stringify({ req_id: `unsub_${Date.now()}`, op: 'unsubscribe', args: activeTopics }));
  }

  /** Subscribes ticker + orderbook + trades + klines for every watched symbol. */
  subscribeAllWatchFeeds() {
    if (!this.watchSymbols.length) return;
    const topics = [];
    this.watchSymbols.forEach(s => {
      topics.push(`tickers.${s}`, `orderbook.50.${s}`, `publicTrade.${s}`);
      // Union of the analysis intervals and whatever the chart is displaying.
      for (const iv of this.allIntervals()) topics.push(`kline.${iv}.${s}`);
    });
    this.sendSubscribe(topics);
  }

  /**
   * Changes which symbol's chart/tape is displayed. No resubscription needed — every
   * watched symbol already has a live feed regardless of focus — this just points the
   * REST historical-kline fetch (for the chart) at the newly focused symbol.
   */
  setSymbol(newSymbol) {
    const sym = newSymbol.toUpperCase();
    if (sym === this.symbol) return;
    this.symbol = sym;
    this.loadHistoricalKlines();
  }

  switchSymbol(newSymbol, category, interval) {
    if (interval && interval !== this.klineInterval) {
      this.setKlineInterval(interval);
    }
    if (category && category !== this.category) {
      this.category = category;
      this.disconnect();
      this.connect();
      return;
    }
    this.setSymbol(newSymbol);
  }

  setCategory(newCategory) {
    if (newCategory === this.category) return;
    this.category = newCategory;
    this.disconnect();
    this.connect();
  }

  setNetwork(newNetwork) {
    if (newNetwork === this.network) return;
    this.network = newNetwork;
    this.disconnect();
    this.connect();
  }

  /** Interval is shared across the whole watchlist — every symbol's engine analyzes the same granularity. */
  /** Every kline interval that must stay subscribed: analysis + display. */
  allIntervals() {
    return Array.from(new Set([...this.analysisIntervals, this.klineInterval]));
  }

  setKlineInterval(interval) {
    if (this.klineInterval === interval) return;
    // Only drop the old display interval if the engine does not also need it —
    // unsubscribing an analysis timeframe because the chart moved away from it
    // would blind the engine.
    if (!this.analysisIntervals.includes(this.klineInterval)) {
      this.sendUnsubscribe(this.watchSymbols.map(s => `kline.${this.klineInterval}.${s}`));
    }
    this.klineInterval = interval;
    this.loadHistoricalKlines();
    this.sendSubscribe(this.watchSymbols.map(s => `kline.${this.klineInterval}.${s}`));
  }

  async loadHistoricalKlines() {
    const candles = await this.fetchHistoricalKlines(this.symbol, this.klineInterval);
    if (candles.length) this.emit('historicalKlines', candles);
  }

  /** Fetches 200 historical candles for any symbol/interval — used to bootstrap every
   * watched symbol's engine instantly instead of waiting several real minutes for live
   * ticks to accumulate. */
  async fetchHistoricalKlines(symbol, interval, limit = 300) {
    try {
      const isTestnet = this.network === 'testnet';
      const host = isTestnet ? 'https://api-testnet.bybit.com' : 'https://api.bybit.com';
      const url = `${host}/v5/market/kline?category=${this.category}&symbol=${symbol}&interval=${interval}&limit=${limit}`;

      const res = await fetch(url);
      if (!res.ok) return [];
      const json = await parseBybitJson(res, `fetchHistoricalKlines(${symbol})`);

      if (json.retCode === 0 && json.result && Array.isArray(json.result.list)) {
        // Bybit returns newest first, reverse for chronological order
        const rawList = json.result.list.slice().reverse();
        return rawList.map(item => ({
          start: parseInt(item[0]),
          open: parseFloat(item[1]),
          high: parseFloat(item[2]),
          low: parseFloat(item[3]),
          close: parseFloat(item[4]),
          volume: parseFloat(item[5])
        }));
      }
    } catch (e) {
      console.log(`Historical kline fetch skipped for ${symbol}:`, e.message);
    }
    return [];
  }

  async fetchOlderHistory(oldestTimestamp) {
    if (this.isLoadingOlder || !oldestTimestamp) return;
    this.isLoadingOlder = true;

    try {
      const isTestnet = this.network === 'testnet';
      const host = isTestnet ? 'https://api-testnet.bybit.com' : 'https://api.bybit.com';
      // Bybit end parameter: filter candles where start < end
      const end = oldestTimestamp - 1;
      const url = `${host}/v5/market/kline?category=${this.category}&symbol=${this.symbol}&interval=${this.klineInterval}&limit=200&end=${end}`;

      const res = await fetch(url);
      if (!res.ok) {
        this.isLoadingOlder = false;
        return;
      }
      const json = await parseBybitJson(res, `fetchOlderHistory(${this.symbol})`);

      if (json.retCode === 0 && json.result && Array.isArray(json.result.list) && json.result.list.length > 0) {
        const rawList = json.result.list.slice().reverse();
        const olderCandles = rawList.map(item => ({
          start: parseInt(item[0]),
          open: parseFloat(item[1]),
          high: parseFloat(item[2]),
          low: parseFloat(item[3]),
          close: parseFloat(item[4]),
          volume: parseFloat(item[5])
        }));
        this.emit('olderHistoricalKlines', olderCandles);
      }
    } catch (e) {
      console.warn('Failed to load older historical klines:', e);
    } finally {
      this.isLoadingOlder = false;
    }
  }

  handleMessage(msg) {
    // Notify packet inspector
    this.emit('packet', msg);

    // 1. Handle Pong / Heartbeat response
    if (msg.op === 'ping' || msg.op === 'pong' || msg.ret_msg === 'pong') {
      if (this.pingSentTime > 0) {
        this.latency = Math.round(performance.now() - this.pingSentTime);
        this.emit('status', { status: 'connected', latency: this.latency, url: this.getEndpoint() });
      }
      return;
    }

    // 2. Handle Subscription confirmation
    if (msg.op === 'subscribe') {
      if (!msg.success) {
        console.warn('Subscription error:', msg.ret_msg);
      }
      return;
    }

    // 3. Handle Topic Data — every topic fires for whichever watched symbol it belongs to,
    // regardless of which symbol is currently focused in the chart.
    const topic = msg.topic || '';

    if (topic.startsWith('tickers.')) {
      const symbol = (msg.data && msg.data.symbol) || topic.slice('tickers.'.length);
      this.emit('ticker', { symbol, data: msg.data, type: msg.type });
      return;
    }

    if (topic.startsWith('orderbook.')) {
      // topic shape: orderbook.50.<SYMBOL>
      const symbol = topic.split('.')[2];
      this.handleOrderbookMessage(msg, symbol);
      return;
    }

    if (topic.startsWith('publicTrade.')) {
      const symbol = topic.slice('publicTrade.'.length);
      if (Array.isArray(msg.data)) this.emit('trade', { symbol, data: msg.data });
      return;
    }

    if (topic.startsWith('kline.')) {
      // topic shape: kline.<interval>.<SYMBOL>
      const parts = topic.split('.');
      const interval = parts[1];
      const symbol = parts[2];
      // The interval is carried on the event so consumers can route each bar to
      // the right timeframe slot instead of assuming there is only one.
      if (Array.isArray(msg.data)) this.emit('kline', { symbol, interval, data: msg.data });
      return;
    }
  }

  handleOrderbookMessage(msg, symbol) {
    const data = msg.data;
    if (!data) return;
    const ob = this.orderbooks[symbol];
    if (!ob) return; // not one of our watched symbols

    if (msg.type === 'snapshot') {
      ob.bids.clear();
      ob.asks.clear();

      if (Array.isArray(data.b)) {
        data.b.forEach(([price, size]) => {
          const s = parseFloat(size);
          if (s > 0) ob.bids.set(parseFloat(price), s);
        });
      }

      if (Array.isArray(data.a)) {
        data.a.forEach(([price, size]) => {
          const s = parseFloat(size);
          if (s > 0) ob.asks.set(parseFloat(price), s);
        });
      }
    } else if (msg.type === 'delta') {
      if (Array.isArray(data.b)) {
        data.b.forEach(([price, size]) => {
          const p = parseFloat(price);
          const s = parseFloat(size);
          if (s <= 0) ob.bids.delete(p); else ob.bids.set(p, s);
        });
      }

      if (Array.isArray(data.a)) {
        data.a.forEach(([price, size]) => {
          const p = parseFloat(price);
          const s = parseFloat(size);
          if (s <= 0) ob.asks.delete(p); else ob.asks.set(p, s);
        });
      }
    }

    ob.lastUpdateId = data.u || Date.now();

    const sortedBids = Array.from(ob.bids.entries()).sort((a, b) => b[0] - a[0]).slice(0, 50);
    const sortedAsks = Array.from(ob.asks.entries()).sort((a, b) => a[0] - b[0]).slice(0, 50);

    this.emit('orderbook', {
      symbol,
      data: { symbol, bids: sortedBids, asks: sortedAsks, u: ob.lastUpdateId },
      type: msg.type
    });
  }

  disconnect() {
    this.isManualClose = true;
    this.stopHeartbeat();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.isConnected = false;
  }
}

window.BybitWebSocketClient = BybitWebSocketClient;
