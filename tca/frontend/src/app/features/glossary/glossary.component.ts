import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';

interface Term {
  term: string;
  short: string;
  body: string;
  example?: string;
}

interface Section {
  id: string;
  title: string;
  intro: string;
  terms: Term[];
}

@Component({
  selector: 'app-glossary',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="page">
      <div class="hero">
        <h1>TCA Glossary</h1>
        <p class="hero-sub">A plain-English guide to Transaction Cost Analysis — what the numbers mean, why they matter, and how they fit together.</p>
      </div>

      <div class="layout">

        <!-- Sticky nav -->
        <nav class="toc">
          <p class="toc-label">Sections</p>
          <ul>
            @for (s of sections; track s.id) {
              <li><a (click)="scrollTo(s.id)">{{ s.title }}</a></li>
            }
          </ul>
        </nav>

        <!-- Content -->
        <div class="content">
          @for (s of sections; track s.id) {
            <section [id]="s.id" class="section">
              <h2>{{ s.title }}</h2>
              <p class="section-intro">{{ s.intro }}</p>
              <div class="term-grid">
                @for (t of s.terms; track t.term) {
                  <div class="term-card">
                    <div class="term-name">{{ t.term }}</div>
                    <div class="term-short">{{ t.short }}</div>
                    <div class="term-body">{{ t.body }}</div>
                    @if (t.example) {
                      <div class="term-example">
                        <span class="ex-label">Example</span> {{ t.example }}
                      </div>
                    }
                  </div>
                }
              </div>
            </section>
          }
        </div>

      </div>
    </div>
  `,
  styles: [`
    :host { display: block; background: #0f1923; min-height: 100vh; color: #d0dde8; }
    .page { max-width: 1200px; margin: 0 auto; padding: 0 2rem 4rem; }

    .hero { padding: 2.5rem 0 2rem; border-bottom: 1px solid #2a3f55; margin-bottom: 2rem; }
    h1 { margin: 0 0 0.6rem; font-size: 2rem; color: #e0b44a; }
    .hero-sub { margin: 0; color: #7a8fa6; font-size: 1rem; max-width: 68ch; line-height: 1.6; }

    .layout { display: flex; gap: 2.5rem; align-items: flex-start; }

    /* TOC */
    .toc { position: sticky; top: 1.5rem; flex: 0 0 200px; }
    .toc-label { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em;
        color: #7a8fa6; margin: 0 0 0.5rem; }
    .toc ul { list-style: none; margin: 0; padding: 0; }
    .toc li { margin-bottom: 0.2rem; }
    .toc a { display: block; padding: 0.3rem 0.6rem; border-radius: 4px; color: #7a8fa6;
        text-decoration: none; font-size: 0.82rem; border-left: 2px solid transparent; cursor: pointer; }
    .toc a:hover { color: #e0b44a; border-left-color: #e0b44a; background: #1a2533; }

    /* Sections */
    .content { flex: 1; min-width: 0; }
    .section { margin-bottom: 3rem; scroll-margin-top: 1.5rem; }
    h2 { font-size: 1.15rem; color: #e0b44a; margin: 0 0 0.4rem;
        padding-bottom: 0.4rem; border-bottom: 1px solid #2a3f55; }
    .section-intro { color: #7a8fa6; font-size: 0.88rem; margin: 0 0 1.25rem; max-width: 72ch; line-height: 1.6; }

    /* Term cards */
    .term-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 0.75rem; }
    .term-card { background: #131f2e; border: 1px solid #2a3f55; border-radius: 6px;
        padding: 1rem 1.1rem; }
    .term-name { font-size: 0.88rem; font-weight: 700; color: #e0b44a; margin-bottom: 0.2rem; }
    .term-short { font-size: 0.82rem; color: #a0c0d0; margin-bottom: 0.5rem;
        font-style: italic; }
    .term-body { font-size: 0.82rem; color: #c0cdd8; line-height: 1.6; }
    .term-example { margin-top: 0.6rem; font-size: 0.78rem; color: #7a8fa6;
        background: #1a2533; border-radius: 4px; padding: 0.4rem 0.6rem; line-height: 1.5; }
    .ex-label { color: #e0a040; font-weight: 600; margin-right: 0.3rem; }
  `],
})
export class GlossaryComponent {
  scrollTo(id: string): void {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  readonly sections: Section[] = [
    {
      id: 'what-is-tca',
      title: 'What is Transaction Cost Analysis?',
      intro: 'TCA is the process of measuring what it actually cost to execute a trade, and comparing that cost against what it should have cost. Think of it like checking your receipt after a supermarket shop: you knew the listed prices, so you can see whether you paid more or less than expected — and why.',
      terms: [
        {
          term: 'Transaction Cost Analysis (TCA)',
          short: 'Measuring the true cost of executing a trade.',
          body: 'Every time a fund buys or sells a security, it incurs costs beyond the commission paid to the broker. The price of the security itself moves while the trade is being executed — sometimes in your favour, sometimes against you. TCA quantifies all of these costs so you can hold your brokers and algorithms accountable.',
        },
        {
          term: 'Basis Point (bps)',
          short: 'The unit of measurement for all costs in TCA.',
          body: 'One basis point = 0.01% = one hundredth of one percent. It is the standard unit for expressing small price differences in financial markets. TCA uses bps because costs like slippage are tiny fractions of a price, and percentages like 0.03% are harder to compare than "3 bps".',
          example: 'A stock priced at €100 moves 5 bps — that is a move of €0.05.',
        },
        {
          term: 'Arrival Price',
          short: 'The price of the security the moment you decided to trade.',
          body: 'This is the mid-point of the bid/ask spread captured at the instant the order was sent to the market. It is the TCA "starting gun" — every cost is measured relative to this price. You cannot trade at the arrival price (it is an observation, not a fill), so all costs measure how far reality deviated from this ideal.',
          example: 'You send a buy order when the stock is trading at €50.00. That €50.00 is your arrival price.',
        },
        {
          term: 'Average Fill Price',
          short: 'The price you actually paid, averaged across all your executions.',
          body: 'Large orders are rarely filled in one go. They are split into many small child orders that execute at different prices throughout the day. The average fill price is the volume-weighted average across all those individual fills — the "true" price you ended up paying.',
          example: 'You buy 10,000 shares: 5,000 at €50.02 and 5,000 at €50.06. Avg fill = €50.04.',
        },
      ],
    },
    {
      id: 'slippage',
      title: 'Slippage & Execution Cost',
      intro: 'Slippage measures the gap between where you wanted to trade (the arrival price) and where you actually traded (the average fill). It is the single most important number in TCA — a negative number means the trade cost you money relative to the moment you decided to act.',
      terms: [
        {
          term: 'Arrival Slippage (bps)',
          short: 'How much the trade cost relative to the arrival price.',
          body: 'Calculated as: (avg fill price − arrival price) / arrival price × 10,000. For a BUY order, if the stock went up while you were buying, you paid more than you intended — that is adverse slippage and shows as a negative number (a cost). For a SELL order, if the price went down while you were selling, that is also adverse. Positive arrival slippage is favourable — you got a better price than when you started.',
          example: 'Arrival price €50.00, avg fill €50.05 on a BUY → −10 bps (you paid 10 bps more than the arrival price).',
        },
        {
          term: 'Market Impact (bps)',
          short: 'The price movement your own order caused.',
          body: 'When you buy a large quantity, your own buying pressure pushes the price up — that is market impact. It is the cost of revealing your intention to the market. Large orders have higher market impact because the market "sees" persistent buying and marks the price up. It is distinct from slippage: slippage is the total cost, market impact is the portion caused by your own footprint.',
          example: 'A 500,000-share buy order in a stock that normally trades 2M shares/day will move the price — perhaps 15–30 bps — just because of your own activity.',
        },
        {
          term: 'Commission (bps)',
          short: 'The explicit fee paid to the broker.',
          body: 'The straightforward, agreed-upon fee PrivateBank charges for executing the order, expressed in basis points of notional. This is the "visible" cost on the trade confirmation. In TCA, it is important but usually the smallest component — market impact and slippage often dwarf commission.',
          example: 'A typical institutional equity commission might be 5–10 bps.',
        },
        {
          term: 'Total Cost (bps)',
          short: 'All-in execution cost: slippage + impact + commission.',
          body: 'The complete picture of what the trade cost. Arrival slippage captures price movement, market impact isolates your own contribution to that movement, and commission is the explicit fee. Adding all three gives you the true cost of executing the order — the number you compare across brokers and algorithms.',
        },
        {
          term: 'Execution Quality',
          short: 'A summary rating of how well the order was executed.',
          body: 'Orders are ranked by arrival slippage within their asset class and assigned a rating: Excellent (top quartile — very low cost), Good (above average), Fair (below average), or Poor (bottom quartile — high cost). This lets a portfolio manager quickly scan a list of orders without reading bps numbers for every row.',
        },
      ],
    },
    {
      id: 'benchmarks',
      title: 'Price Benchmarks',
      intro: 'A benchmark is a reference price you compare your fill against. Different benchmarks answer different questions. Arrival price asks "did I act at the right time?" — VWAP asks "did I trade in line with the market rhythm?"',
      terms: [
        {
          term: 'VWAP — Volume-Weighted Average Price',
          short: 'The average price the entire market traded at, weighted by volume.',
          body: 'VWAP is calculated by taking every trade that happened in the market during the day, multiplying price by volume, summing everything up, and dividing by total volume. It represents the "fair" price of the day. Many passive institutional strategies aim to trade at or better than VWAP — if you beat VWAP as a buyer, you paid less than the average market participant.',
          example: 'If 1M shares traded between 9am–5pm at various prices, VWAP might be €50.10. If you averaged €50.05, you beat VWAP by 10 bps.',
        },
        {
          term: 'VWAP Slippage (bps)',
          short: 'How your fill compared to the market\'s average price for the day.',
          body: 'Calculated the same way as arrival slippage, but versus VWAP instead of arrival price. Beating VWAP as a buyer (paying less than VWAP) is generally considered good execution. VWAP slippage is useful for assessing "schedule risk" — did you trade too aggressively (paying up) or too passively (missing the opportunity)?',
        },
        {
          term: 'TWAP — Time-Weighted Average Price',
          short: 'The simple average of prices taken at regular intervals throughout the day.',
          body: 'Unlike VWAP (which weights by volume), TWAP weights every minute of the trading day equally. It is used when you want to participate evenly through time rather than track volume patterns. TWAP algorithms spread trading uniformly, making them predictable but potentially expensive in trending markets.',
        },
        {
          term: 'Close Price / Close Slippage',
          short: 'How your fill compared to the end-of-day closing price.',
          body: 'The official closing auction price. Some fund benchmarks (especially mutual funds) are struck against the close, so comparing fills against close price shows whether the execution added or destroyed value relative to the fund\'s own NAV calculation.',
        },
      ],
    },
    {
      id: 'algorithms',
      title: 'Execution Algorithms',
      intro: 'Rather than sending one huge order to the market (which would telegraph your intention and move the price), brokers use algorithms to slice orders into small pieces and execute them intelligently over time. Each algorithm has a different strategy for balancing speed against market impact.',
      terms: [
        {
          term: 'IS — Implementation Shortfall',
          short: 'Trades fast early to lock in the arrival price, slowing down as impact rises.',
          body: 'The IS algorithm tries to minimise the gap between your arrival price and your average fill — i.e., it directly targets arrival slippage. It trades more aggressively at the start when market impact is low, and slows down as the price moves against you. Best used when you believe the stock will continue to move in the same direction (momentum) and you want to get the trade done quickly.',
          example: 'You want to buy 200,000 shares and the price is rising. IS would front-load execution to catch the current price before it rises further.',
        },
        {
          term: 'VWAP Algorithm',
          short: 'Spreads trading to match the market\'s expected volume pattern.',
          body: 'The VWAP algo uses historical intraday volume profiles (e.g. high volume at open and close, lower at midday) to schedule child orders proportionally. The goal is to hit the day\'s VWAP. It is a "passive" strategy — it does not try to beat the market, it tries to blend in. Lower market impact, but you may miss out if the price moves strongly.',
        },
        {
          term: 'TWAP Algorithm',
          short: 'Spreads trading evenly through time, regardless of volume.',
          body: 'Divides the order into equal time slices and executes evenly. Even simpler and more predictable than VWAP. Used when you have no view on intraday volume patterns, or when you want fully mechanical execution. Very transparent and auditable.',
        },
        {
          term: 'POV — Percentage of Volume',
          short: 'Participates as a fixed percentage of whatever the market is trading.',
          body: 'Rather than following a schedule, POV tracks live market volume and continuously adjusts its execution rate to stay at a target participation level (e.g. always 10% of market volume). If the market is quiet, POV trades slowly; if volume spikes, POV trades faster. Minimises impact but completion time is uncertain.',
        },
        {
          term: 'SNIPER / Opportunistic',
          short: 'Waits for favourable liquidity moments and strikes quickly.',
          body: 'Rather than spreading orders over time, Sniper algorithms sit quietly and look for large blocks of liquidity in dark pools or at favourable prices, then execute quickly when found. Very low market impact when it works, but uncertain timing. Often used for large, difficult orders in less liquid stocks.',
        },
        {
          term: 'Participation Rate',
          short: 'What fraction of market volume your order consumed.',
          body: 'If the total market traded 1,000,000 shares and your order accounted for 100,000 of them, your participation rate is 10%. Higher participation = more aggressive = faster execution but higher market impact. Lower participation = passive = less impact but slower, with more timing risk.',
          example: 'An IS algo on an urgent order might run at 25–30% participation. A passive VWAP algo might target 5–8%.',
        },
      ],
    },
    {
      id: 'alpha-decay',
      title: 'Alpha Decay by Volatility Regime',
      intro: '"Alpha" is the excess return of a trade — the signal that motivated the portfolio manager to trade in the first place. Alpha decay measures how quickly that opportunity evaporates after you start trading. If alpha decays fast, you must execute quickly. If it decays slowly, you can afford to be patient and reduce costs.',
      terms: [
        {
          term: 'Alpha',
          short: 'The expected profit from trading on a signal before others act on it.',
          body: 'When a portfolio manager decides to buy a stock, it is usually because they believe it is undervalued and will rise. That expected rise is the "alpha" — the profit opportunity. But as you execute (buying pushes the price up) and as other market participants notice the same signal, the opportunity erodes. TCA measures how much of that alpha survived execution.',
        },
        {
          term: 'Arrival vs T+30m (bps)',
          short: 'How much the price moved 30 minutes after order submission.',
          body: 'Measured as (price at T+30 minutes − arrival price) / arrival price × 10,000. A large negative number on a BUY order means the price rose strongly after you started buying — your signal was correct, but you may have missed some of the move. A positive number means the price fell, which is bad: your signal may have been wrong, or you executed too slowly and the market moved against you.',
          example: 'You buy at €50.00 arrival. 30 minutes later the stock is at €50.30. Alpha T+30m = +60 bps — the signal was correct and strong.',
        },
        {
          term: 'Arrival vs T+1h (bps)',
          short: 'Price movement measured one hour after order submission.',
          body: 'Comparing T+30m to T+1h shows whether the initial move continued or reversed. If T+30m is −20 bps (price rose) and T+1h is −15 bps, the move partially reversed — momentum was short-lived. If T+1h is even more negative (say −35 bps), momentum continued — you should have been even more aggressive.',
        },
        {
          term: 'Arrival vs T+4h (bps)',
          short: 'Price movement measured four hours after order submission.',
          body: 'By four hours, most intraday execution should be complete. If alpha is still strongly present at T+4h, it suggests the signal had "slow burn" characteristics — patient execution (TWAP/VWAP) would have been fine. If alpha is near zero by T+4h, the opportunity was short-lived and aggressive execution (IS) was the right choice.',
        },
        {
          term: 'Volatility Regime',
          short: 'How turbulent the market was on the day of the order.',
          body: 'Classified as LOW, MEDIUM, or HIGH based on the Z-score of 30-day realised volatility at the time of order submission. Regime matters because costs and alpha decay behave very differently in calm vs volatile markets. In HIGH volatility regimes, slippage is larger, market impact is harder to estimate, and alpha decays faster — all of which affect algorithm choice.',
          example: 'March 2020 (COVID crash) would be a HIGH regime. A quiet August might be LOW.',
        },
      ],
    },
    {
      id: 'venue-sor',
      title: 'Venues & Smart Order Routing',
      intro: 'European equity markets are fragmented — the same stock can trade on dozens of different venues simultaneously. Choosing where to route your order has a significant impact on cost and certainty of execution. Smart Order Routers (SOR) automate this choice in real time.',
      terms: [
        {
          term: 'Execution Venue',
          short: 'The marketplace where your order was actually matched and filled.',
          body: 'In Europe, a single stock like Siemens can trade on the Frankfurt Stock Exchange (primary), XETRA, Euronext, BATS, Turquoise, Cboe, Chi-X, and several dark pools simultaneously. Each venue has different liquidity, speed, and cost characteristics. The venue you choose directly affects slippage and market impact.',
        },
        {
          term: 'MIC Code',
          short: 'The four-letter identifier for a venue (ISO 10383 standard).',
          body: 'MIC stands for Market Identifier Code. Every regulated trading venue in the world has a unique four-letter MIC. XETR = XETRA (Deutsche Börse electronic), XLON = London Stock Exchange, XPAR = Euronext Paris. MIF II requires MIC codes on all transaction reports.',
          example: 'XCSE = Copenhagen Stock Exchange, XAMS = Euronext Amsterdam.',
        },
        {
          term: 'Smart Order Router (SOR)',
          short: 'Software that automatically picks the best venue for each child order.',
          body: 'A SOR looks at live order books across all available venues, compares prices, queue positions, and fill probability, and routes each small child order to the optimal destination in milliseconds. A good SOR can meaningfully reduce execution costs compared to always sending to the primary exchange.',
        },
        {
          term: 'Dark Pool',
          short: 'A private trading venue where orders are hidden until matched.',
          body: 'Unlike lit exchanges where all orders are visible in the order book, dark pools hide orders until execution. This means large orders can trade without telegraphing their size to the market — reducing market impact. The trade-off is uncertainty: you may not get filled, and you must wait for a counterparty to appear on the other side.',
        },
        {
          term: 'Fill Rate',
          short: 'What percentage of your order was actually executed at a given venue.',
          body: 'A fill rate of 100% means every share you sent to that venue was filled. A fill rate of 60% means 40% of the quantity was rejected or returned unfilled (perhaps because the liquidity disappeared before your order reached the front of the queue). Higher fill rates generally indicate better venue quality or better SOR calibration.',
          example: 'You route 10,000 shares to a dark pool. 7,000 get filled, 3,000 are returned. Fill rate = 70%.',
        },
        {
          term: 'Bid/Ask Spread (bps)',
          short: 'The gap between the best buy price and the best sell price at a venue.',
          body: 'The spread is the implicit cost of immediacy — if you need to trade right now, you buy at the ask (higher) or sell at the bid (lower). A tight spread (2 bps) means liquid venue with low cost of immediacy. A wide spread (20 bps) means illiquid — crossing the spread is expensive. Spread is a measure of venue quality: tighter is better.',
        },
      ],
    },
    {
      id: 'mifid',
      title: 'MiFID II & Regulatory Reporting',
      intro: 'MiFID II (Markets in Financial Instruments Directive II) is European regulation requiring investment firms to report every transaction to their national regulator. The goal is transparency — regulators want to see what was traded, where, at what price, and why certain transparency rules were waived. Non-compliance carries significant fines.',
      terms: [
        {
          term: 'MiFID II',
          short: 'The EU law requiring detailed transaction reporting and best execution proof.',
          body: 'Since January 2018, every EU investment firm must report each trade to a national competent authority (NCA) within the day of execution. The report includes the instrument, price, quantity, venue, counterparty, and various flags. It also requires firms to demonstrate "best execution" — that they took all sufficient steps to get the best possible result for the client.',
        },
        {
          term: 'RTS 27',
          short: 'The specific regulation defining execution quality reporting standards.',
          body: 'RTS stands for Regulatory Technical Standard. RTS 27 requires execution venues to publish quarterly reports on execution quality (price, costs, speed, likelihood of execution). Investment firms use RTS 27 reports to compare venues when designing their order routing policies. The "RTS 27 Category" column in the MiFID export classifies each trade by where and how it was executed.',
        },
        {
          term: 'RTS 27 Category',
          short: 'Classification of how the trade was executed: Lit, Dark, OTC, or SI.',
          body: 'Lit = traded on a transparent regulated market where all orders are visible. Dark = traded in a dark pool where pre-trade prices were not disclosed. OTC = over-the-counter, a bilateral deal negotiated directly with a counterparty outside any exchange. SI = traded with a Systematic Internaliser (the broker trading against you from their own book).',
          example: '"Lit" is the default for most exchange-traded equities. "Dark" is used for large block trades in dark pools.',
        },
        {
          term: 'Pre-Trade Waiver',
          short: 'A regulatory permission to hide your order from the public order book before trading.',
          body: 'Under MiFID II, all orders must normally be displayed publicly before trading (pre-trade transparency). A waiver excuses you from this requirement. There are several types: LRGS (Large-in-Scale — your order is too big and displaying it would move the market), SIZE (above standard market size), and ILQD (the instrument is illiquid). Waivers must be justified and reported.',
        },
        {
          term: 'LRGS — Large-in-Scale',
          short: 'The waiver for orders too large to display without moving the market.',
          body: 'LRGS (Large in Scale) is a pre-trade waiver that allows a very large order to be executed in a dark pool or negotiated bilaterally, without first being shown in the public order book. The EU defines minimum sizes for LRGS eligibility per instrument. Without this waiver, displaying a massive order would immediately reveal your intention and cause adverse price movement.',
          example: 'A €50M buy order in a stock that typically trades €5M/day would qualify for LRGS — showing it publicly would immediately push the price up.',
        },
        {
          term: 'LRGS Deferral',
          short: 'Permission to delay publishing a trade\'s details after execution.',
          body: 'Post-trade transparency normally requires immediate publication of every trade. A deferral allows publication to be delayed (typically 2–4 weeks for very large or illiquid trades). This is different from the pre-trade LRGS waiver: the waiver hides the order before execution; the deferral delays publication of the completed trade. Both must be flagged in the transaction report.',
        },
        {
          term: 'Systematic Internaliser (SI)',
          short: 'A broker that trades against clients from its own book on a regular basis.',
          body: 'An SI is an investment firm that, on an organised, frequent, systematic, and substantial basis, deals on its own account when executing client orders — i.e. it takes the other side of the trade itself rather than routing to an exchange. SIs must quote firm prices for standard-size orders and are subject to specific MiFID II transparency rules. The SI Flag in the data indicates the counterparty was acting as an SI.',
        },
        {
          term: 'OTC — Over the Counter',
          short: 'A trade negotiated directly between two parties, not on a public exchange.',
          body: 'OTC trades bypass exchanges entirely. Common for bonds, derivatives, and large equity blocks where the parties negotiate price and terms directly (usually via phone or electronic negotiation platforms). OTC trades still must be reported under MiFID II, but they are exempt from pre-trade transparency requirements since there is no public order book to display them on.',
        },
        {
          term: 'Notional Value',
          short: 'The total money value of the trade.',
          body: 'Notional = average fill price × quantity. Expressed in millions of euros (€M) in this platform. Regulators use notional to measure systemic risk and to set thresholds for waivers (e.g. LRGS thresholds are defined in notional terms). It is also used internally to size positions and calculate fees.',
        },
      ],
    },
    {
      id: 'asset-classes',
      title: 'Asset Classes',
      intro: 'PrivateBank\'s TCA covers four instrument classes. Each has different market structures, liquidity profiles, and regulatory treatment — which is why TCA metrics are always compared within the same asset class rather than across them.',
      terms: [
        {
          term: 'Equity (Cash Equities & ETFs)',
          short: 'Shares of companies, traded on lit exchanges and dark pools.',
          body: 'The most liquid and transparent asset class covered. Cash equities trade continuously on multiple European lit venues (LSE, XETRA, Euronext) and dark pools simultaneously. ETFs (Exchange Traded Funds) trade like shares but represent baskets of assets. TCA for equities is the most developed — VWAP, TWAP, and arrival slippage are all well-established benchmarks.',
        },
        {
          term: 'Equity Future',
          short: 'A contract to buy or sell an equity index or single stock at a future date.',
          body: 'Futures trade on derivatives exchanges (Eurex, ICE). They are used to gain or hedge equity exposure efficiently — one Eurex DAX future controls €25 × the DAX index level. TCA for futures uses EDSP (Exchange Delivery Settlement Price) as an additional benchmark. Market impact behaves differently from cash equities because futures are centrally cleared and the order book is shallower.',
        },
        {
          term: 'Fixed Income',
          short: 'Bonds — loans to governments or companies that pay regular interest.',
          body: 'Bonds are predominantly OTC instruments with no centralised exchange and much wider spreads than equities. Liquidity varies enormously — German Bunds are very liquid, a 10-year Italian corporate bond might be very illiquid. TCA for fixed income uses yield-based metrics (DV01 — the price sensitivity to a 1 basis point move in yield) alongside price-based slippage. Best execution is harder to prove without a transparent order book.',
        },
        {
          term: 'FX Derivative',
          short: 'Contracts to exchange currencies at an agreed rate on a future date.',
          body: 'FX forwards and options used to manage currency risk in a portfolio. Spot FX is excluded from MiFID II reporting scope for PrivateBank — this platform covers only FX derivatives (forwards, options, swaps). Costs are measured in forward points (the difference between spot and forward exchange rates) and spread vs mid-market. Liquidity is generally high for major currency pairs.',
        },
      ],
    },
  ];
}
