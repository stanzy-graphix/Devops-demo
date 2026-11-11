//+------------------------------------------------------------------+
//|                                                      SMC_Bot.mq5 |
//|  Smart Money Concepts Expert Advisor for MetaTrader 5.           |
//|                                                                  |
//|  Attach to the chart of the entry timeframe (e.g. EURUSD M15).   |
//|  Model: higher-timeframe bias -> liquidity sweep -> BOS/CHoCH    |
//|  with a fair value gap -> limit order at the order block,        |
//|  stop beyond the block, target at a fixed reward:risk.           |
//|  All decisions are made on closed candles only.                  |
//|                                                                  |
//|  Not financial advice. Test in the Strategy Tester and on a      |
//|  demo account before using real money.                           |
//+------------------------------------------------------------------+
#property version   "1.00"
#property description "SMC: HTF bias, liquidity sweep, BOS/CHoCH, FVG, order block entry"

#include <Trade/Trade.mqh>

input group "Strategy"
input int             InpSwingLen         = 3;         // Swing length (bars each side)
input ENUM_TIMEFRAMES InpHTF              = PERIOD_H4; // Higher timeframe for bias
input int             InpHTFSwingLen      = 3;         // HTF swing length
input bool            InpUseHTFBias       = true;      // Trade only with HTF structure
input bool            InpRequireSweep     = true;      // Require liquidity sweep before the break
input int             InpSweepLookback    = 30;        // Max bars between sweep and break
input int             InpSweepReclaimBars = 3;         // Bars allowed to close back past a swept level
input bool            InpRequireFVG       = true;      // Require fair value gap in the breaking leg
input int             InpOBLookback       = 5;         // Bars to search back for the order block
input double          InpEntryLevel       = 0.0;       // Entry inside block (0 = edge, 0.5 = middle)
input double          InpRR               = 2.0;       // Reward : risk
input double          InpSLBufferATR      = 0.1;       // Stop buffer beyond the block (ATRs)
input double          InpMinStopATR       = 0.5;       // Minimum stop size (ATRs)
input double          InpMaxStopATR       = 3.0;       // Maximum stop size (ATRs)
input int             InpATRLen           = 14;        // ATR length
input int             InpExpiryBars       = 24;        // Cancel unfilled order after this many bars
input int             InpSessionStart     = 9;         // Session start hour, BROKER SERVER time
input int             InpSessionEnd       = 19;        // Session end hour (start = end: trade all day)

input group "Risk"
input double          InpRiskPercent         = 1.0;    // Risk per trade, % of equity
input int             InpMaxOpen             = 1;      // Max positions + pending orders
input double          InpMaxDailyLossPercent = 3.0;    // Stop new trades after this daily loss, %
input double          InpBreakevenAtR        = 0.0;    // Move stop to entry at this many R (0 = off)

input group "Other"
input long            InpMagic      = 20261111;        // Magic number
input int             InpWarmupBars = 1500;            // History bars used to build structure at start

//--- a swing point (kind: +1 swing high, -1 swing low)
struct Swing
  {
   int               index;
   double            price;
   int               kind;
   bool              broken;
   int               piercedAt;
   bool              done;
   bool              valid;
  };

//+------------------------------------------------------------------+
//| Market structure tracker: swings, BOS/CHoCH, liquidity sweeps.   |
//| Fed one closed bar at a time; a swing is only known once         |
//| swing-length bars have closed after it.                          |
//+------------------------------------------------------------------+
class CTracker
  {
public:
   int               n;
   int               reclaim;
   int               maxLiq;
   double            o[];
   double            h[];
   double            l[];
   double            c[];
   int               count;
   int               trend;            // +1 bullish, -1 bearish, 0 undefined
   Swing             lastHigh;
   Swing             lastLow;
   Swing             liq[];
   int               liqCount;
   //--- results of the last Update()
   int               breakDir;         // +1 / -1 / 0
   bool              breakIsChoch;
   int               breakSwingIndex;
   bool              sweepUp;          // sell-side liquidity taken (bullish)
   bool              sweepDown;        // buy-side liquidity taken (bearish)

   void              Init(const int swingLen, const int reclaimBars)
     {
      n = swingLen;
      reclaim = reclaimBars;
      maxLiq = 10;
      count = 0;
      trend = 0;
      liqCount = 0;
      breakDir = 0;
      breakIsChoch = false;
      breakSwingIndex = 0;
      sweepUp = false;
      sweepDown = false;
      ZeroMemory(lastHigh);
      ZeroMemory(lastLow);
      ArrayResize(liq, maxLiq + 4);
      ArrayResize(o, 0);
      ArrayResize(h, 0);
      ArrayResize(l, 0);
      ArrayResize(c, 0);
     }

   void              AddSwing(const int idx, const double price, const int kind)
     {
      Swing s;
      s.index = idx;
      s.price = price;
      s.kind = kind;
      s.broken = false;
      s.piercedAt = -1;
      s.done = false;
      s.valid = true;
      if(kind == 1)
         lastHigh = s;
      else
         lastLow = s;
      if(liqCount < ArraySize(liq))
        {
         liq[liqCount] = s;
         liqCount++;
        }
     }

   void              Update(const double po, const double ph, const double pl, const double pc)
     {
      int i = count;
      if(ArraySize(h) <= i)
        {
         int ns = i + 1024;
         ArrayResize(o, ns);
         ArrayResize(h, ns);
         ArrayResize(l, ns);
         ArrayResize(c, ns);
        }
      o[i] = po;
      h[i] = ph;
      l[i] = pl;
      c[i] = pc;
      count++;
      breakDir = 0;
      sweepUp = false;
      sweepDown = false;

      //--- structure break: a close beyond the most recent confirmed swing
      if(lastHigh.valid && !lastHigh.broken && pc > lastHigh.price)
        {
         lastHigh.broken = true;
         breakDir = 1;
         breakIsChoch = (trend == -1);
         breakSwingIndex = lastHigh.index;
         trend = 1;
        }
      else
         if(lastLow.valid && !lastLow.broken && pc < lastLow.price)
           {
            lastLow.broken = true;
            breakDir = -1;
            breakIsChoch = (trend == 1);
            breakSwingIndex = lastLow.index;
            trend = -1;
           }

      //--- liquidity sweeps: price trades through a swing, then closes back inside
      int keep = 0;
      for(int k = 0; k < liqCount; k++)
        {
         Swing s = liq[k];
         bool pierced = (s.kind == -1) ? (pl < s.price) : (ph > s.price);
         if(s.piercedAt < 0 && pierced)
            s.piercedAt = i;
         if(s.piercedAt >= 0)
           {
            bool reclaimed = (s.kind == -1) ? (pc > s.price) : (pc < s.price);
            if(reclaimed)
              {
               s.done = true;
               if(s.kind == -1)
                  sweepUp = true;
               else
                  sweepDown = true;
              }
            else
               if(i - s.piercedAt + 1 >= reclaim)
                  s.done = true;
           }
         if(!s.done)
           {
            liq[keep] = s;
            keep++;
           }
        }
      liqCount = keep;
      if(liqCount > maxLiq)
        {
         int shift = liqCount - maxLiq;
         for(int k = 0; k < maxLiq; k++)
            liq[k] = liq[k + shift];
         liqCount = maxLiq;
        }

      //--- confirm the swing candidate that now has n bars on its right
      int cand = i - n;
      if(cand >= n)
        {
         double maxAll = h[cand - n];
         double minAll = l[cand - n];
         for(int j = cand - n; j <= i; j++)
           {
            if(h[j] > maxAll)
               maxAll = h[j];
            if(l[j] < minAll)
               minAll = l[j];
           }
         double maxLeft = h[cand - n];
         double minLeft = l[cand - n];
         for(int j = cand - n; j < cand; j++)
           {
            if(h[j] > maxLeft)
               maxLeft = h[j];
            if(l[j] < minLeft)
               minLeft = l[j];
           }
         if(h[cand] == maxAll && h[cand] > maxLeft)
            AddSwing(cand, h[cand], 1);
         if(l[cand] == minAll && l[cand] < minLeft)
            AddSwing(cand, l[cand], -1);
        }
     }
  };

//--- globals
CTrade   trade;
CTracker ltf;
CTracker htf;
double   g_atr          = 0.0;
int      g_atrN         = 0;
int      g_lastSweepUp  = -1000000000;
int      g_lastSweepDn  = -1000000000;
datetime g_lastProcessed = 0;   // open time of the last closed bar fed to the strategy
datetime g_lastBar0     = 0;    // open time of the forming bar when we last ran
datetime g_lastHtfTime  = 0;    // open time of the last HTF bar fed
bool     g_warmed       = false;

//+------------------------------------------------------------------+
int OnInit()
  {
   if(PeriodSeconds(InpHTF) <= PeriodSeconds(_Period))
     {
      Print("SMC_Bot: the higher timeframe must be above the chart timeframe.");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(InpSwingLen < 1 || InpHTFSwingLen < 1 || InpRR <= 0 || InpRiskPercent <= 0)
     {
      Print("SMC_Bot: invalid inputs.");
      return INIT_PARAMETERS_INCORRECT;
     }
   trade.SetExpertMagicNumber(InpMagic);
   ltf.Init(InpSwingLen, InpSweepReclaimBars);
   htf.Init(InpHTFSwingLen, 3);
   g_atr = 0.0;
   g_atrN = 0;
   g_lastSweepUp = -1000000000;
   g_lastSweepDn = -1000000000;
   g_lastProcessed = 0;
   g_lastBar0 = 0;
   g_lastHtfTime = 0;
   g_warmed = false;
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   Comment("");
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   if(!g_warmed && !Warmup())
      return;

   ManageBreakeven();

   datetime t0 = iTime(_Symbol, _Period, 0);
   if(t0 == 0 || t0 == g_lastBar0)
      return;

   //--- a new bar opened: process every bar that closed since the last run
   MqlRates r[];
   int got = CopyRates(_Symbol, _Period, g_lastProcessed + 1, t0 - 1, r);
   if(got <= 0)
      return;
   g_lastBar0 = t0;
   for(int k = 0; k < got; k++)
     {
      if(r[k].time <= g_lastProcessed || r[k].time >= t0)
         continue;
      ProcessBar(r[k], k == got - 1);
      g_lastProcessed = r[k].time;
     }

   ExpirePending();
   if(DailyLossHit())
      CancelPending(0);

   string bias = (htf.trend == 1) ? "bullish" : (htf.trend == -1) ? "bearish" : "undefined";
   string ls = (ltf.trend == 1) ? "bullish" : (ltf.trend == -1) ? "bearish" : "undefined";
   Comment("SMC_Bot | HTF bias: ", bias, " | entry TF structure: ", ls);
  }

//+------------------------------------------------------------------+
//| Build structure from history without trading.                    |
//+------------------------------------------------------------------+
bool Warmup()
  {
   MqlRates r[];
   int got = CopyRates(_Symbol, _Period, 1, InpWarmupBars, r);
   if(got < 50)
      return false;
   MqlRates hr[];
   int hg = CopyRates(_Symbol, InpHTF, 0, 500, hr);
   if(hg <= 0)
      return false;

   int hsec = PeriodSeconds(InpHTF);
   datetime firstCut = r[0].time;
   for(int k = 0; k < hg; k++)
     {
      if(hr[k].time + hsec <= firstCut)
        {
         htf.Update(hr[k].open, hr[k].high, hr[k].low, hr[k].close);
         g_lastHtfTime = hr[k].time;
        }
     }
   if(g_lastHtfTime == 0)
      g_lastHtfTime = hr[0].time - hsec;

   for(int k = 0; k < got; k++)
      ProcessBar(r[k], false);
   g_lastProcessed = r[got - 1].time;
   g_warmed = true;
   Print("SMC_Bot: warmed up on ", got, " bars. HTF bias ", htf.trend);
   return true;
  }

//+------------------------------------------------------------------+
//| Feed HTF bars that have fully closed by 'cutoff'.                |
//+------------------------------------------------------------------+
void FeedHTF(const datetime cutoff)
  {
   int hsec = PeriodSeconds(InpHTF);
   if(cutoff < g_lastHtfTime + 2 * hsec)
      return;
   MqlRates hr[];
   int g = CopyRates(_Symbol, InpHTF, g_lastHtfTime + hsec, cutoff, hr);
   for(int k = 0; k < g; k++)
     {
      if(hr[k].time > g_lastHtfTime && hr[k].time + hsec <= cutoff)
        {
         htf.Update(hr[k].open, hr[k].high, hr[k].low, hr[k].close);
         g_lastHtfTime = hr[k].time;
        }
     }
  }

//+------------------------------------------------------------------+
//| Feed one closed entry-timeframe bar to the strategy.             |
//+------------------------------------------------------------------+
void ProcessBar(const MqlRates &bar, const bool allowTrade)
  {
   FeedHTF(bar.time + PeriodSeconds(_Period));

   //--- running (Wilder) ATR
   double tr = bar.high - bar.low;
   if(ltf.count > 0)
     {
      double pc = ltf.c[ltf.count - 1];
      tr = MathMax(tr, MathMax(MathAbs(bar.high - pc), MathAbs(bar.low - pc)));
     }
   g_atrN++;
   int kk = MathMin(g_atrN, InpATRLen);
   if(g_atrN == 1)
      g_atr = tr;
   else
      g_atr += (tr - g_atr) / kk;

   ltf.Update(bar.open, bar.high, bar.low, bar.close);
   int i = ltf.count - 1;
   if(ltf.sweepUp)
      g_lastSweepUp = i;
   if(ltf.sweepDown)
      g_lastSweepDn = i;

   if(ltf.breakDir == 0)
      return;
   int d = ltf.breakDir;
   if(allowTrade)
      CancelPending(-d);            // structure moved against orders on the other side

   double entry = 0, sl = 0, tp = 0;
   if(Setup(d, ltf.breakSwingIndex, i, bar.time, entry, sl, tp) && allowTrade)
     {
      Print("SMC_Bot: ", (ltf.breakIsChoch ? "CHoCH " : "BOS "), (d == 1 ? "up" : "down"),
            " -> ", (d == 1 ? "BUY" : "SELL"), " limit ", DoubleToString(entry, _Digits),
            " sl ", DoubleToString(sl, _Digits), " tp ", DoubleToString(tp, _Digits));
      PlaceOrder(d, entry, sl, tp);
     }
  }

//+------------------------------------------------------------------+
bool InSession(const datetime t)
  {
   if(InpSessionStart == InpSessionEnd)
      return true;
   MqlDateTime dt;
   TimeToStruct(t, dt);
   if(InpSessionStart < InpSessionEnd)
      return (dt.hour >= InpSessionStart && dt.hour < InpSessionEnd);
   return (dt.hour >= InpSessionStart || dt.hour < InpSessionEnd);
  }

//+------------------------------------------------------------------+
//| Entry model. d = direction of the structure break at bar i,      |
//| start = index of the swing that was broken.                      |
//+------------------------------------------------------------------+
bool Setup(const int d, const int start, const int i, const datetime t,
           double &entry, double &sl, double &tp)
  {
   if(InpUseHTFBias && htf.trend != d)
      return false;
   if(!InSession(t))
      return false;
   int lastSweep = (d == 1) ? g_lastSweepUp : g_lastSweepDn;
   if(InpRequireSweep && i - lastSweep > InpSweepLookback)
      return false;

   //--- origin of the leg that broke structure: the extreme since the broken swing
   int origin = start;
   for(int k = start; k <= i; k++)
     {
      if(d == 1 && ltf.l[k] < ltf.l[origin])
         origin = k;
      if(d == -1 && ltf.h[k] > ltf.h[origin])
         origin = k;
     }
   double legLo = ltf.l[origin];
   double legHi = ltf.h[origin];
   for(int k = origin; k <= i; k++)
     {
      if(ltf.h[k] > legHi)
         legHi = ltf.h[k];
      if(ltf.l[k] < legLo)
         legLo = ltf.l[k];
     }

   //--- displacement: a fair value gap (3-bar imbalance) inside the leg
   bool fvg = false;
   for(int k = MathMax(origin + 1, 2); k <= i; k++)
     {
      if((d == 1 && ltf.l[k] > ltf.h[k - 2]) || (d == -1 && ltf.h[k] < ltf.l[k - 2]))
        {
         fvg = true;
         break;
        }
     }
   if(InpRequireFVG && !fvg)
      return false;

   //--- order block: last opposite-coloured candle before the leg
   int ob = -1;
   for(int j = origin; j > origin - InpOBLookback && j >= 0; j--)
     {
      if((d == 1 && ltf.c[j] < ltf.o[j]) || (d == -1 && ltf.c[j] > ltf.o[j]))
        {
         ob = j;
         break;
        }
     }
   if(ob < 0)
      return false;

   double buf = InpSLBufferATR * g_atr;
   if(d == 1)
     {
      double top = ltf.h[ob];
      double bot = ltf.l[ob];
      for(int k = ob; k <= origin; k++)
         if(ltf.l[k] < bot)
            bot = ltf.l[k];
      entry = top - InpEntryLevel * (top - bot);
      sl = bot - buf;
     }
   else
     {
      double bot = ltf.l[ob];
      double top = ltf.h[ob];
      for(int k = ob; k <= origin; k++)
         if(ltf.h[k] > top)
            top = ltf.h[k];
      entry = bot + InpEntryLevel * (top - bot);
      sl = top + buf;
     }

   double risk = MathAbs(entry - sl);
   double mid = (legLo + legHi) / 2.0;
   if((entry - mid) * d > 0)                 // longs in discount, shorts in premium
      return false;
   if((ltf.c[i] - entry) * d <= 0)           // price is already back inside the block
      return false;
   if(risk < InpMinStopATR * g_atr || risk > InpMaxStopATR * g_atr)
      return false;
   tp = entry + d * InpRR * risk;
   return true;
  }

//+------------------------------------------------------------------+
int CountOurs()
  {
   int total = 0;
   for(int k = PositionsTotal() - 1; k >= 0; k--)
     {
      ulong tk = PositionGetTicket(k);
      if(tk == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagic)
         total++;
     }
   for(int k = OrdersTotal() - 1; k >= 0; k--)
     {
      ulong tk = OrderGetTicket(k);
      if(tk == 0)
         continue;
      if(OrderGetString(ORDER_SYMBOL) == _Symbol && OrderGetInteger(ORDER_MAGIC) == InpMagic)
         total++;
     }
   return total;
  }

//+------------------------------------------------------------------+
//| Delete our pending orders. side: +1 buys, -1 sells, 0 all.       |
//+------------------------------------------------------------------+
void CancelPending(const int side)
  {
   for(int k = OrdersTotal() - 1; k >= 0; k--)
     {
      ulong tk = OrderGetTicket(k);
      if(tk == 0)
         continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol || OrderGetInteger(ORDER_MAGIC) != InpMagic)
         continue;
      long type = OrderGetInteger(ORDER_TYPE);
      int os = (type == ORDER_TYPE_BUY_LIMIT) ? 1 : (type == ORDER_TYPE_SELL_LIMIT) ? -1 : 0;
      if(os != 0 && (side == 0 || side == os))
         trade.OrderDelete(tk);
     }
  }

//+------------------------------------------------------------------+
void ExpirePending()
  {
   for(int k = OrdersTotal() - 1; k >= 0; k--)
     {
      ulong tk = OrderGetTicket(k);
      if(tk == 0)
         continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol || OrderGetInteger(ORDER_MAGIC) != InpMagic)
         continue;
      datetime setup = (datetime)OrderGetInteger(ORDER_TIME_SETUP);
      if(iBarShift(_Symbol, _Period, setup) >= InpExpiryBars)
         trade.OrderDelete(tk);
     }
  }

//+------------------------------------------------------------------+
//| True once today's loss (closed + floating) reaches the limit.    |
//+------------------------------------------------------------------+
bool DailyLossHit()
  {
   if(InpMaxDailyLossPercent <= 0)
      return false;
   datetime dayStart = iTime(_Symbol, PERIOD_D1, 0);
   if(dayStart == 0 || !HistorySelect(dayStart, TimeCurrent() + 60))
      return false;
   double pl = 0.0;
   for(int k = HistoryDealsTotal() - 1; k >= 0; k--)
     {
      ulong tk = HistoryDealGetTicket(k);
      if(tk == 0)
         continue;
      long type = HistoryDealGetInteger(tk, DEAL_TYPE);
      if(type != DEAL_TYPE_BUY && type != DEAL_TYPE_SELL)
         continue;
      pl += HistoryDealGetDouble(tk, DEAL_PROFIT) + HistoryDealGetDouble(tk, DEAL_SWAP)
            + HistoryDealGetDouble(tk, DEAL_COMMISSION);
     }
   double startBalance = AccountInfoDouble(ACCOUNT_BALANCE) - pl;
   return (AccountInfoDouble(ACCOUNT_EQUITY) <= startBalance * (1.0 - InpMaxDailyLossPercent / 100.0));
  }

//+------------------------------------------------------------------+
//| Lots such that a stop-out loses InpRiskPercent of equity.        |
//+------------------------------------------------------------------+
double CalcLots(const int d, const double entry, const double sl)
  {
   ENUM_ORDER_TYPE ot = (d == 1) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double lossPerLot = 0.0;
   if(!OrderCalcProfit(ot, _Symbol, 1.0, entry, sl, lossPerLot) || lossPerLot >= 0.0)
      return 0.0;
   double riskMoney = AccountInfoDouble(ACCOUNT_EQUITY) * InpRiskPercent / 100.0;
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0.0)
      return 0.0;
   double lots = MathFloor(riskMoney / (-lossPerLot) / step + 1e-9) * step;
   if(lots > vmax)
      lots = vmax;
   if(lots < vmin)
      return 0.0;                   // the minimum lot would risk more than allowed
   double margin = 0.0;
   if(OrderCalcMargin(ot, _Symbol, lots, entry, margin) && margin > AccountInfoDouble(ACCOUNT_MARGIN_FREE))
      return 0.0;
   int volDigits = (int)MathMax(0.0, MathCeil(-MathLog10(step) - 1e-9));
   return NormalizeDouble(lots, volDigits);
  }

//+------------------------------------------------------------------+
void PlaceOrder(const int d, double entry, double sl, double tp)
  {
   if(DailyLossHit())
     {
      Print("SMC_Bot: daily loss limit reached, signal skipped.");
      return;
     }
   if(CountOurs() >= InpMaxOpen)
     {
      Print("SMC_Bot: max open trades/orders reached, signal skipped.");
      return;
     }
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   //--- levels come from bid candles; shorts are stopped out and closed at the ask
   if(d == -1)
     {
      sl += ask - bid;
      tp += ask - bid;
     }
   entry = NormalizeDouble(entry, _Digits);
   sl = NormalizeDouble(sl, _Digits);
   tp = NormalizeDouble(tp, _Digits);

   double minDist = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   if((d == 1 && entry >= ask - minDist) || (d == -1 && entry <= bid + minDist))
     {
      Print("SMC_Bot: price too close to the entry level, signal skipped.");
      return;
     }
   if(MathAbs(entry - sl) < minDist || MathAbs(tp - entry) < minDist)
     {
      Print("SMC_Bot: stop or target closer than the broker allows, signal skipped.");
      return;
     }
   double lots = CalcLots(d, entry, sl);
   if(lots <= 0.0)
     {
      Print("SMC_Bot: position size is below the minimum lot or margin is short, signal skipped.");
      return;
     }
   bool ok = (d == 1) ? trade.BuyLimit(lots, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, "SMC")
             : trade.SellLimit(lots, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, "SMC");
   if(!ok)
      Print("SMC_Bot: order failed: ", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
  }

//+------------------------------------------------------------------+
//| Move the stop to entry once price has gone InpBreakevenAtR * R.  |
//+------------------------------------------------------------------+
void ManageBreakeven()
  {
   if(InpBreakevenAtR <= 0.0)
      return;
   for(int k = PositionsTotal() - 1; k >= 0; k--)
     {
      ulong tk = PositionGetTicket(k);
      if(tk == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = PositionGetDouble(POSITION_SL);
      double tp = PositionGetDouble(POSITION_TP);
      if(tp == 0.0)
         continue;
      double risk = MathAbs(tp - open) / InpRR;
      if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
        {
         if(sl < open && SymbolInfoDouble(_Symbol, SYMBOL_BID) - open >= InpBreakevenAtR * risk)
            trade.PositionModify(tk, NormalizeDouble(open, _Digits), tp);
        }
      else
        {
         if((sl > open || sl == 0.0) && open - SymbolInfoDouble(_Symbol, SYMBOL_ASK) >= InpBreakevenAtR * risk)
            trade.PositionModify(tk, NormalizeDouble(open, _Digits), tp);
        }
     }
  }
//+------------------------------------------------------------------+
