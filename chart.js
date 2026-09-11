/**
 * Professional Canvas Candlestick & Market Depth Chart Engine
 * Renders high-frequency real-time cryptocurrency price action, OHLC bars, volume histograms,
 * dynamic EMA(9)/EMA(21) overlays, crosshairs, and cumulative depth curves.
 */

class CandlestickChartEngine {
  constructor(canvasId, tooltipId) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas.getContext('2d');
    this.tooltip = document.getElementById(tooltipId);
    this.container = this.canvas.parentElement;

    // Data Storage
    this.allCandles = []; // All chronologically sorted candles { start, open, high, low, close, volume }
    this.currentPrice = 0;
    this.interval = '1';

    // Viewport & Scale State
    this.width = 0;
    this.height = 0;
    this.dpr = window.devicePixelRatio || 1;
    this.rightPadding = 75; // Right price axis width
    this.bottomPadding = 26; // Bottom time axis height
    this.volumeHeightRatio = 0.20; // 20% of height for volume

    // Navigation (Pan & Zoom)
    this.visibleCount = 65; // Number of candles visible
    this.minVisible = 15;
    this.maxVisible = 250;
    this.panOffset = 0; // 0 = anchored to rightmost (latest). Positive = scrolled into history.

    // Vertical Price Scaling & Scrolling
    this.isManualPriceScale = false;
    this.manualMinPrice = 0;
    this.manualMaxPrice = 0;

    // Mouse & Touch Drag State
    this.mouseX = -1;
    this.mouseY = -1;
    this.isHovering = false;
    this.isDragging = false;
    this.isDraggingPriceAxis = false;
    this.dragStartX = 0;
    this.dragStartY = 0;
    this.startPanOffset = 0;
    this.dragStartMinPrice = 0;
    this.dragStartMaxPrice = 0;
    this.isShiftKey = false;

    // Technical Indicators
    this.ema9 = [];
    this.ema21 = [];

    // History pagination callback
    this.onNeedMoreHistory = null;
    this.hasRequestedHistory = false;

    this.init();
  }

  init() {
    this.resize();
    window.addEventListener('resize', () => this.resize());

    if (window.ResizeObserver) {
      const ro = new ResizeObserver(() => this.resize());
      ro.observe(this.container);
    }

    // Canvas Mouse Events for Navigation & Crosshair
    this.canvas.addEventListener('mousedown', (e) => this.handleMouseDown(e));
    window.addEventListener('mousemove', (e) => this.handleMouseMove(e));
    window.addEventListener('mouseup', () => this.handleMouseUp());
    this.canvas.addEventListener('wheel', (e) => this.handleWheel(e), { passive: false });
    this.canvas.addEventListener('dblclick', () => this.resetView());
    this.canvas.addEventListener('mouseleave', () => this.handleMouseLeave());
  }

  resize() {
    if (!this.container) return;
    const rect = this.container.getBoundingClientRect();
    this.width = rect.width;
    this.height = rect.height;

    this.canvas.width = this.width * this.dpr;
    this.canvas.height = this.height * this.dpr;
    this.canvas.style.width = `${this.width}px`;
    this.canvas.style.height = `${this.height}px`;

    this.ctx.scale(this.dpr, this.dpr);
    this.render();
  }

  handleMouseDown(e) {
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const plotWidth = this.width - this.rightPadding;

    this.dragStartX = e.clientX;
    this.dragStartY = e.clientY;
    this.isShiftKey = e.shiftKey;

    if (x >= plotWidth) {
      // Dragging on Right Price Axis -> Vertical Price Scaling
      this.isDraggingPriceAxis = true;
      this.isManualPriceScale = true;
      const range = this.getPriceRange(plotWidth, this.height - this.bottomPadding);
      this.dragStartMinPrice = range.minPrice;
      this.dragStartMaxPrice = range.maxPrice;
      this.container.classList.add('resizing-v');
    } else {
      // Dragging on Main Chart Body -> Horizontal Pan
      this.isDragging = true;
      this.startPanOffset = this.panOffset;
      this.container.classList.add('grabbing');

      if (e.shiftKey) {
        // Shift + Drag = Vertical Price Scroll
        this.isManualPriceScale = true;
        const range = this.getPriceRange(plotWidth, this.height - this.bottomPadding);
        this.dragStartMinPrice = range.minPrice;
        this.dragStartMaxPrice = range.maxPrice;
      }
    }
  }

  handleMouseMove(e) {
    const rect = this.canvas.getBoundingClientRect();
    this.mouseX = e.clientX - rect.left;
    this.mouseY = e.clientY - rect.top;
    const plotWidth = this.width - this.rightPadding;

    // Update cursor style
    if (!this.isDragging && !this.isDraggingPriceAxis) {
      if (this.mouseX >= plotWidth) {
        this.container.style.cursor = 'ns-resize';
      } else {
        this.container.style.cursor = 'grab';
      }
    }

    if (this.isDraggingPriceAxis) {
      // Scale vertical price range up/down
      const dy = e.clientY - this.dragStartY;
      const factor = 1 + dy * 0.005;
      const centerPrice = (this.dragStartMinPrice + this.dragStartMaxPrice) / 2;
      const halfSpan = ((this.dragStartMaxPrice - this.dragStartMinPrice) / 2) * factor;

      this.manualMinPrice = centerPrice - Math.max(halfSpan, 0.0001);
      this.manualMaxPrice = centerPrice + Math.max(halfSpan, 0.0001);
      this.render();
      return;
    }

    if (this.isDragging) {
      if (this.isShiftKey) {
        // Shift + Drag = Vertical Scroll / Pan
        const dy = e.clientY - this.dragStartY;
        const range = this.dragStartMaxPrice - this.dragStartMinPrice;
        const priceDelta = (dy / (this.height - this.bottomPadding)) * range;
        this.manualMinPrice = this.dragStartMinPrice + priceDelta;
        this.manualMaxPrice = this.dragStartMaxPrice + priceDelta;
        this.render();
        return;
      }

      // Horizontal Pan
      const dx = e.clientX - this.dragStartX;
      const slotWidth = plotWidth / this.visibleCount;
      const candlesMoved = dx / slotWidth;

      const maxOffset = Math.max(this.allCandles.length - 5, 0);
      this.panOffset = Math.max(0, Math.min(this.startPanOffset + candlesMoved, maxOffset));

      // Check if near beginning of loaded candles to load older history
      const visibleEnd = Math.max(this.allCandles.length - 1 - Math.round(this.panOffset), 0);
      const visibleStart = Math.max(visibleEnd - this.visibleCount + 1, 0);

      if (visibleStart <= 20 && !this.hasRequestedHistory && this.onNeedMoreHistory && this.allCandles.length > 0) {
        this.hasRequestedHistory = true;
        this.onNeedMoreHistory(this.allCandles[0].start);
      }

      this.render();
      return;
    }

    // Normal Hover (Crosshair)
    if (this.mouseX >= 0 && this.mouseX <= this.width && this.mouseY >= 0 && this.mouseY <= this.height) {
      this.isHovering = true;
      this.render();
    } else {
      this.isHovering = false;
      if (this.tooltip) this.tooltip.style.display = 'none';
      this.render();
    }
  }

  handleMouseUp() {
    this.isDragging = false;
    this.isDraggingPriceAxis = false;
    this.container.classList.remove('grabbing', 'resizing-v');
    this.container.style.cursor = 'grab';
  }

  handleMouseLeave() {
    this.isHovering = false;
    this.isDragging = false;
    this.isDraggingPriceAxis = false;
    if (this.tooltip) this.tooltip.style.display = 'none';
    this.container.classList.remove('grabbing', 'resizing-v');
    this.render();
  }

  handleWheel(e) {
    e.preventDefault();
    const plotWidth = this.width - this.rightPadding;

    if (this.mouseX >= plotWidth) {
      // Wheel over price axis: Vertical Price Zoom
      this.isManualPriceScale = true;
      const range = this.getPriceRange(plotWidth, this.height - this.bottomPadding);
      const zoomFactor = e.deltaY > 0 ? 1.1 : 0.9;
      const center = (range.minPrice + range.maxPrice) / 2;
      const half = ((range.maxPrice - range.minPrice) / 2) * zoomFactor;
      this.manualMinPrice = center - half;
      this.manualMaxPrice = center + half;
      this.render();
      return;
    }

    // Wheel over chart: Horizontal Time Zoom
    const zoomDelta = e.deltaY > 0 ? 6 : -6;
    const prevCount = this.visibleCount;
    this.visibleCount = Math.max(this.minVisible, Math.min(this.visibleCount + zoomDelta, this.maxVisible));

    // Keep zoom centered
    if (this.panOffset > 0) {
      const diff = this.visibleCount - prevCount;
      this.panOffset = Math.max(0, this.panOffset - diff / 2);
    }

    this.render();
  }

  zoomIn() {
    this.visibleCount = Math.max(this.minVisible, this.visibleCount - 8);
    this.render();
  }

  zoomOut() {
    this.visibleCount = Math.min(this.maxVisible, this.visibleCount + 8);
    this.render();
  }

  resetView() {
    this.panOffset = 0;
    this.isManualPriceScale = false;
    this.hasRequestedHistory = false;
    this.visibleCount = 65;
    this.render();
  }

  clear() {
    this.allCandles = [];
    this.ema9 = [];
    this.ema21 = [];
    this.panOffset = 0;
    this.isManualPriceScale = false;
    this.hasRequestedHistory = false;
    this.render();
  }

  clearData() {
    this.clear();
  }

  draw() {
    this.render();
  }

  setCandles(candles, interval) {
    if (interval) this.interval = interval;
    this.allCandles = candles.slice();
    this.panOffset = 0;
    this.hasRequestedHistory = false;
    this.recalculateIndicators();
    this.render();
  }

  prependCandles(olderCandles) {
    if (!olderCandles || olderCandles.length === 0) return;
    this.hasRequestedHistory = false;

    // Filter out any overlap
    const existingStarts = new Set(this.allCandles.map(c => c.start));
    const newItems = olderCandles.filter(c => !existingStarts.has(c.start));

    if (newItems.length > 0) {
      this.allCandles = newItems.concat(this.allCandles);
      // Adjust panOffset by added items so viewport position doesn't shift
      this.panOffset += newItems.length;
      this.recalculateIndicators();
      this.render();
    }
  }

  updateCandle(kline) {
    const start = parseInt(kline.start || kline.time);
    const open = parseFloat(kline.open);
    const high = parseFloat(kline.high);
    const low = parseFloat(kline.low);
    const close = parseFloat(kline.close);
    const volume = parseFloat(kline.volume);

    this.currentPrice = close;

    if (this.allCandles.length === 0) {
      this.allCandles.push({ start, open, high, low, close, volume });
    } else {
      const last = this.allCandles[this.allCandles.length - 1];
      if (last.start === start) {
        last.high = Math.max(last.high, high);
        last.low = Math.min(last.low, low);
        last.close = close;
        last.volume = volume;
      } else if (start > last.start) {
        this.allCandles.push({ start, open, high, low, close, volume });
      }
    }

    this.recalculateIndicators();
    this.render();
  }

  updateFromTrade(price, size) {
    if (this.allCandles.length === 0) return;
    this.currentPrice = price;
    const last = this.allCandles[this.allCandles.length - 1];
    last.high = Math.max(last.high, price);
    last.low = Math.min(last.low, price);
    last.close = price;
    last.volume += size;
    this.render();
  }

  calculateEMA(period) {
    const k = 2 / (period + 1);
    const ema = [];
    if (this.allCandles.length === 0) return ema;

    let prevEMA = this.allCandles[0].close;
    ema.push(prevEMA);

    for (let i = 1; i < this.allCandles.length; i++) {
      const curEMA = this.allCandles[i].close * k + prevEMA * (1 - k);
      ema.push(curEMA);
      prevEMA = curEMA;
    }
    return ema;
  }

  recalculateIndicators() {
    this.ema9 = this.calculateEMA(9);
    this.ema21 = this.calculateEMA(21);

    const ema9El = document.getElementById('ema9Val');
    const ema21El = document.getElementById('ema21Val');
    const volEl = document.getElementById('curVolVal');

    if (ema9El && this.ema9.length > 0) {
      ema9El.textContent = this.formatPrice(this.ema9[this.ema9.length - 1]);
    }
    if (ema21El && this.ema21.length > 0) {
      ema21El.textContent = this.formatPrice(this.ema21[this.ema21.length - 1]);
    }
    if (volEl && this.allCandles.length > 0) {
      volEl.textContent = this.formatNumber(this.allCandles[this.allCandles.length - 1].volume);
    }
  }

  getVisibleRange(plotWidth, pricePlotHeight) {
    if (this.allCandles.length === 0) {
      return { visibleCandles: [], startIndex: 0, endIndex: 0, minPrice: 0, maxPrice: 1, maxVolume: 1 };
    }

    const endIndex = Math.max(this.allCandles.length - 1 - Math.round(this.panOffset), 0);
    const startIndex = Math.max(endIndex - this.visibleCount + 1, 0);
    const visibleCandles = this.allCandles.slice(startIndex, endIndex + 1);

    let minPrice = Infinity;
    let maxPrice = -Infinity;
    let maxVolume = 0;

    visibleCandles.forEach(c => {
      if (c.low < minPrice) minPrice = c.low;
      if (c.high > maxPrice) maxPrice = c.high;
      if (c.volume > maxVolume) maxVolume = c.volume;
    });

    if (this.isManualPriceScale && this.manualMaxPrice > this.manualMinPrice) {
      minPrice = this.manualMinPrice;
      maxPrice = this.manualMaxPrice;
    } else {
      const padding = (maxPrice - minPrice) * 0.07 || 1;
      minPrice -= padding;
      maxPrice += padding;
    }

    return { visibleCandles, startIndex, endIndex, minPrice, maxPrice, maxVolume };
  }

  getPriceRange(plotWidth, pricePlotHeight) {
    return this.getVisibleRange(plotWidth, pricePlotHeight);
  }

  render() {
    if (!this.ctx || this.width === 0 || this.height === 0) return;
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.width, this.height);

    if (this.allCandles.length === 0) {
      this.drawPlaceholder();
      return;
    }

    const plotWidth = this.width - this.rightPadding;
    const plotHeight = this.height - this.bottomPadding;
    const volumeHeight = plotHeight * this.volumeHeightRatio;
    const pricePlotHeight = plotHeight - volumeHeight - 12;

    const { visibleCandles, startIndex, minPrice, maxPrice, maxVolume } = this.getVisibleRange(plotWidth, pricePlotHeight);
    if (visibleCandles.length === 0) return;

    const priceRange = maxPrice - minPrice || 1;

    const getY = (price) => {
      return pricePlotHeight - ((price - minPrice) / priceRange) * pricePlotHeight + 10;
    };

    const getVolY = (vol) => {
      const barH = (vol / (maxVolume || 1)) * volumeHeight;
      return plotHeight - barH;
    };

    // Use the ACTUAL rendered candle count, not the requested zoom target — when there
    // isn't enough loaded history to fill the zoomed-out view (e.g. right after a symbol
    // switch, or zooming out further than the fetched candle limit), this.visibleCount can
    // exceed visibleCandles.length, which would otherwise squeeze/misalign every candle.
    const slotWidth = plotWidth / visibleCandles.length;
    const barWidth = Math.max(slotWidth * 0.72, 2);

    // 1. Draw Price and Time Grid
    this.drawGrid(ctx, plotWidth, plotHeight, pricePlotHeight, minPrice, maxPrice, visibleCandles, slotWidth);

    // 2. Draw Volume Bars
    visibleCandles.forEach((c, i) => {
      const x = i * slotWidth + slotWidth / 2;
      const isUp = c.close >= c.open;
      const volY = getVolY(c.volume);
      const barH = plotHeight - volY;

      ctx.fillStyle = isUp ? 'rgba(14, 203, 129, 0.25)' : 'rgba(246, 70, 93, 0.25)';
      ctx.fillRect(x - barWidth / 2, volY, barWidth, barH);
    });

    // 3. Draw Candlesticks
    visibleCandles.forEach((c, i) => {
      const x = i * slotWidth + slotWidth / 2;
      const isUp = c.close >= c.open;
      const color = isUp ? '#0ECB81' : '#F6465D';

      const openY = getY(c.open);
      const closeY = getY(c.close);
      const highY = getY(c.high);
      const lowY = getY(c.low);

      // Wick
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(x, highY);
      ctx.lineTo(x, lowY);
      ctx.stroke();

      // Body
      const topY = Math.min(openY, closeY);
      const bodyH = Math.max(Math.abs(closeY - openY), 1.5);
      ctx.fillStyle = color;
      ctx.fillRect(x - barWidth / 2, topY, barWidth, bodyH);
    });

    // 4. Draw EMAs
    this.drawEMA(ctx, this.ema9, startIndex, visibleCandles.length, slotWidth, getY, '#06B6D4');
    this.drawEMA(ctx, this.ema21, startIndex, visibleCandles.length, slotWidth, getY, '#F59E0B');

    // 5. Draw Current Price Baseline & Badge
    if (this.currentPrice > 0) {
      const curY = getY(this.currentPrice);
      if (curY >= 0 && curY <= plotHeight) {
        ctx.strokeStyle = '#3B82F6';
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(0, curY);
        ctx.lineTo(plotWidth, curY);
        ctx.stroke();
        ctx.setLineDash([]);

        // Right Axis Price Badge
        ctx.fillStyle = '#2563EB';
        ctx.fillRect(plotWidth, curY - 10, this.rightPadding, 20);
        ctx.fillStyle = '#FFFFFF';
        ctx.font = 'bold 10px "JetBrains Mono"';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(this.formatPrice(this.currentPrice), plotWidth + 6, curY);
      }
    }

    // 6. Pan Indicator Tag (If scrolled back in time)
    if (this.panOffset > 1) {
      ctx.fillStyle = 'rgba(59, 130, 246, 0.2)';
      ctx.strokeStyle = 'rgba(59, 130, 246, 0.5)';
      ctx.lineWidth = 1;
      const tagText = `Viewing History (${Math.round(this.panOffset)} bars back) - Double-click to Reset`;
      ctx.font = '10px "Inter"';
      const textW = ctx.measureText(tagText).width;
      ctx.fillRect(10, 10, textW + 16, 20);
      ctx.strokeRect(10, 10, textW + 16, 20);
      ctx.fillStyle = '#93C5FD';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(tagText, 18, 20);
    }

    // 7. Crosshair & Tooltip
    if (this.isHovering && this.mouseX >= 0 && this.mouseX <= plotWidth && this.mouseY <= plotHeight) {
      this.drawCrosshair(ctx, plotWidth, plotHeight, pricePlotHeight, minPrice, priceRange, visibleCandles, slotWidth);
    }
  }

  drawGrid(ctx, plotWidth, plotHeight, pricePlotHeight, minPrice, maxPrice, visibleCandles, slotWidth) {
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.04)';
    ctx.lineWidth = 1;
    ctx.fillStyle = '#64748B';
    ctx.font = '10px "JetBrains Mono"';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';

    // Horizontal Price Levels
    const steps = 5;
    for (let i = 0; i <= steps; i++) {
      const y = (pricePlotHeight / steps) * i + 10;
      const price = maxPrice - ((maxPrice - minPrice) / steps) * i;

      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(plotWidth, y);
      ctx.stroke();

      ctx.fillText(this.formatPrice(price), plotWidth + 8, y);
    }

    // Vertical Time Grid & Bottom Axis Labels
    const labelSpacing = Math.max(Math.floor(visibleCandles.length / 6), 1);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';

    for (let i = 0; i < visibleCandles.length; i += labelSpacing) {
      const x = i * slotWidth + slotWidth / 2;
      const candle = visibleCandles[i];
      if (!candle) continue;

      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, plotHeight);
      ctx.stroke();

      const timeLabel = this.formatTime(candle.start);
      ctx.fillText(timeLabel, x, plotHeight + 6);
    }

    // Bottom axis separator line
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.08)';
    ctx.beginPath();
    ctx.moveTo(0, plotHeight);
    ctx.lineTo(this.width, plotHeight);
    ctx.stroke();

    // Right axis separator line
    ctx.beginPath();
    ctx.moveTo(plotWidth, 0);
    ctx.lineTo(plotWidth, this.height);
    ctx.stroke();
  }

  drawEMA(ctx, emaArr, startIndex, visibleLength, slotWidth, getY, color) {
    if (!emaArr || emaArr.length === 0) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.4;
    ctx.beginPath();

    let started = false;
    for (let i = 0; i < visibleLength; i++) {
      const globalIndex = startIndex + i;
      const val = emaArr[globalIndex];
      if (val === undefined || isNaN(val)) continue;

      const x = i * slotWidth + slotWidth / 2;
      const y = getY(val);

      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.stroke();
  }

  drawCrosshair(ctx, plotWidth, plotHeight, pricePlotHeight, minPrice, priceRange, visibleCandles, slotWidth) {
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.22)';
    ctx.setLineDash([3, 3]);
    ctx.lineWidth = 1;

    // Vertical line
    ctx.beginPath();
    ctx.moveTo(this.mouseX, 0);
    ctx.lineTo(this.mouseX, plotHeight);
    ctx.stroke();

    // Horizontal line
    ctx.beginPath();
    ctx.moveTo(0, this.mouseY);
    ctx.lineTo(plotWidth, this.mouseY);
    ctx.stroke();
    ctx.setLineDash([]);

    // Price badge on right axis
    const hoveredPrice = maxPriceFromY(this.mouseY, pricePlotHeight, minPrice, priceRange);
    ctx.fillStyle = '#334155';
    ctx.fillRect(plotWidth, this.mouseY - 9, this.rightPadding, 18);
    ctx.fillStyle = '#FFFFFF';
    ctx.font = '10px "JetBrains Mono"';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(this.formatPrice(hoveredPrice), plotWidth + 6, this.mouseY);

    // Hovered Candle Data
    const localIndex = Math.min(Math.max(Math.floor(this.mouseX / slotWidth), 0), visibleCandles.length - 1);
    const candle = visibleCandles[localIndex];

    if (this.tooltip && candle) {
      const date = new Date(candle.start);
      const dateStr = date.toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
      });
      const change = candle.close - candle.open;
      const changePct = ((change / candle.open) * 100).toFixed(2);
      const isUp = change >= 0;

      this.tooltip.innerHTML = `
        <div style="color: #94A3B8; margin-bottom: 2px;">${dateStr}</div>
        <div>O: <span style="color:#FFF;">${this.formatPrice(candle.open)}</span></div>
        <div>H: <span style="color:#0ECB81;">${this.formatPrice(candle.high)}</span></div>
        <div>L: <span style="color:#F6465D;">${this.formatPrice(candle.low)}</span></div>
        <div>C: <span style="color:${isUp ? '#0ECB81' : '#F6465D'};">${this.formatPrice(candle.close)} (${isUp ? '+' : ''}${changePct}%)</span></div>
        <div>Vol: <span style="color:#A78BFA;">${this.formatNumber(candle.volume)}</span></div>
      `;

      this.tooltip.style.display = 'block';
      let tooltipX = this.mouseX + 16;
      if (tooltipX + 140 > plotWidth) tooltipX = this.mouseX - 150;
      this.tooltip.style.left = `${tooltipX}px`;
      this.tooltip.style.top = `${Math.min(this.mouseY, plotHeight - 120)}px`;
    }

    function maxPriceFromY(y, pHeight, minP, pRange) {
      return minP + ((pHeight - y + 10) / pHeight) * pRange;
    }
  }

  drawPlaceholder() {
    const ctx = this.ctx;
    ctx.fillStyle = '#64748B';
    ctx.font = '13px "Inter"';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('Loading Bybit candlestick history...', this.width / 2, this.height / 2);
  }

  formatTime(ts) {
    const d = new Date(ts);
    if (this.interval === 'D' || this.interval === 'W') {
      return `${d.getMonth() + 1}/${d.getDate()}`;
    }
    const h = String(d.getHours()).padStart(2, '0');
    const m = String(d.getMinutes()).padStart(2, '0');
    return `${h}:${m}`;
  }

  formatPrice(val) {
    if (val === undefined || isNaN(val)) return '--';
    if (val >= 1000) return val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (val >= 1) return val.toFixed(4);
    return val.toFixed(6);
  }

  formatNumber(val) {
    if (val >= 1e9) return (val / 1e9).toFixed(2) + 'B';
    if (val >= 1e6) return (val / 1e6).toFixed(2) + 'M';
    if (val >= 1e3) return (val / 1e3).toFixed(2) + 'K';
    return val.toFixed(2);
  }
}

/**
 * Market Depth Curve Visualizer
 * Draws cumulative bids (green slope) vs cumulative asks (red slope)
 */
class MarketDepthChartEngine {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas.getContext('2d');
    this.container = this.canvas.parentElement;
    this.dpr = window.devicePixelRatio || 1;
    this.width = 0;
    this.height = 0;

    this.bids = []; // [ [price, size], ... ]
    this.asks = []; // [ [price, size], ... ]

    this.init();
  }

  init() {
    this.resize();
    window.addEventListener('resize', () => this.resize());
    if (window.ResizeObserver) {
      const ro = new ResizeObserver(() => this.resize());
      ro.observe(this.container);
    }
  }

  resize() {
    if (!this.container) return;
    const rect = this.container.getBoundingClientRect();
    this.width = rect.width;
    this.height = rect.height;

    this.canvas.width = this.width * this.dpr;
    this.canvas.height = this.height * this.dpr;
    this.canvas.style.width = `${this.width}px`;
    this.canvas.style.height = `${this.height}px`;

    this.ctx.scale(this.dpr, this.dpr);
    this.render();
  }

  update(bids, asks) {
    this.bids = bids || [];
    this.asks = asks || [];
    this.render();
  }

  render() {
    if (!this.ctx || this.width === 0 || this.height === 0) return;
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.width, this.height);

    if (this.bids.length === 0 || this.asks.length === 0) {
      ctx.fillStyle = '#64748B';
      ctx.font = '13px "Inter"';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('Accumulating depth book data...', this.width / 2, this.height / 2);
      return;
    }

    const padding = 20;
    const plotWidth = this.width - padding * 2;
    const plotHeight = this.height - padding * 2;
    const midX = padding + plotWidth / 2;

    // Calculate cumulative depth for bids (reverse sorted: closest to market first)
    const bidPoints = [];
    let cumBid = 0;
    for (let i = 0; i < this.bids.length; i++) {
      cumBid += this.bids[i][1];
      bidPoints.push({ price: this.bids[i][0], cum: cumBid });
    }

    // Cumulative depth for asks
    const askPoints = [];
    let cumAsk = 0;
    for (let i = 0; i < this.asks.length; i++) {
      cumAsk += this.asks[i][1];
      askPoints.push({ price: this.asks[i][0], cum: cumAsk });
    }

    const maxCum = Math.max(cumBid, cumAsk) || 1;

    // Draw Bids Mountain (Left to Center)
    ctx.beginPath();
    ctx.moveTo(padding, this.height - padding);

    const bidStep = (plotWidth / 2) / Math.max(bidPoints.length, 1);
    for (let i = bidPoints.length - 1; i >= 0; i--) {
      const x = midX - (bidPoints.length - 1 - i) * bidStep;
      const y = this.height - padding - (bidPoints[i].cum / maxCum) * plotHeight;
      ctx.lineTo(x, y);
    }
    ctx.lineTo(midX, this.height - padding);
    ctx.closePath();

    const bidGrad = ctx.createLinearGradient(0, 0, 0, this.height);
    bidGrad.addColorStop(0, 'rgba(14, 203, 129, 0.35)');
    bidGrad.addColorStop(1, 'rgba(14, 203, 129, 0.02)');
    ctx.fillStyle = bidGrad;
    ctx.fill();

    ctx.strokeStyle = '#0ECB81';
    ctx.lineWidth = 2;
    ctx.stroke();

    // Draw Asks Mountain (Center to Right)
    ctx.beginPath();
    ctx.moveTo(midX, this.height - padding);

    const askStep = (plotWidth / 2) / Math.max(askPoints.length, 1);
    for (let i = 0; i < askPoints.length; i++) {
      const x = midX + i * askStep;
      const y = this.height - padding - (askPoints[i].cum / maxCum) * plotHeight;
      ctx.lineTo(x, y);
    }
    ctx.lineTo(this.width - padding, this.height - padding);
    ctx.closePath();

    const askGrad = ctx.createLinearGradient(0, 0, 0, this.height);
    askGrad.addColorStop(0, 'rgba(246, 70, 93, 0.35)');
    askGrad.addColorStop(1, 'rgba(246, 70, 93, 0.02)');
    ctx.fillStyle = askGrad;
    ctx.fill();

    ctx.strokeStyle = '#F6465D';
    ctx.lineWidth = 2;
    ctx.stroke();

    // Mid Market Divider
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.15)';
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(midX, padding);
    ctx.lineTo(midX, this.height - padding);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

window.CandlestickChartEngine = CandlestickChartEngine;
window.MarketDepthChartEngine = MarketDepthChartEngine;
