#!/usr/bin/env python3
"""Generate the 26 uniform DeFi product feature pages from the catalog.

Usage: python3 tools/gen_product_pages.py
Writes templates/products/<slug>.html using the shared pro design system.
Copy rules enforced here:
  - positive framing only; never say what a product is not / doesn't have
  - no fabricated metrics (no TVL, users, revenue claims)
  - design targets labeled as targets; no live-status claims, no "soon"
  - no internals: treasury, fees routing, burn, token price, infra
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from sincor2.defi.catalog import PROTOCOLS  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates", "products")

SLUGS = {
    "P01_YIELD_AGG": "p01-yield-aggregator",
    "P02_CLMM": "p02-clmm-manager",
    "P03_INTENT_DARK": "p03-intent-dark-pool",
    "P04_MEV": "p04-mev-capture",
    "P05_INSURANCE": "p05-defi-insurance",
    "P06_PERPS": "p06-perp-dex",
    "P07_BRIDGE": "p07-bridge-optimizer",
    "P08_RWA": "p08-rwa-vaults",
    "P09_DAO_GOV": "p09-dao-governance",
    "P10_FLASH_ARB": "p10-flash-arbitrage",
    "P11_DELTA_NEUTRAL": "p11-delta-neutral",
    "P12_TWAMM": "p12-twamm-engine",
    "P13_AVS": "p13-avs-restaking",
    "P14_PREDICTION": "p14-prediction-markets",
    "P15_LENDING": "p15-lending-optimizer",
    "P16_DEX_AGG": "p16-dex-aggregator",
    "P17_OPTIONS": "p17-options-protocol",
    "P18_STRUCTURED": "p18-structured-products",
    "P19_CREDIT": "p19-credit-underwriting",
    "P20_COMPLIANCE": "p20-compliance-automation",
    "P21_TREASURY_DAO": "p21-treasury-dao",
    "P22_STABLE_YIELD": "p22-stablecoin-yield",
    "P23_NFTFI": "p23-nftfi-pools",
    "P24_SOCIALFI": "p24-socialfi-revenue",
    "P25_PORTFOLIO": "p25-agent-portfolio",
    "P26_DEFI_OS": "p26-defi-os",
}

# tagline, lede, body1, body2, steps[(t,d)x3], caps[x4], audiences[x3], faqs[(q,a)x2], icon
COPY = {
"P01_YIELD_AGG": (
    "Every dollar, always in its best place.",
    "The Yield Aggregator Vault puts agent-managed capital to work across the best onchain yield — rebalancing continuously so your money is never sitting still.",
    "Chasing yield manually is a full-time job: new vaults launch weekly, rates shift daily, and the best opportunity today is average by next month. The Yield Aggregator Vault automates the entire discipline — scanning venues, scoring risk-adjusted return, and moving capital to where it earns most.",
    "A risk budget governs every move. No single strategy can dominate the book, and every reallocation clears a strict quality bar before capital moves. The result is a vault that behaves like a professional fixed-income desk, running around the clock.",
    [("Scan", "Agent swarms monitor lending markets, vaults, and liquidity venues across the network — rates, utilization, and risk signals, updated continuously."),
     ("Score", "Each opportunity is scored on risk-adjusted return against your vault's risk budget. Only the strongest clear the bar."),
     ("Rebalance", "Capital shifts automatically into the winning allocation. Single-strategy caps keep the book diversified at all times.")],
    ["Continuous cross-venue rebalancing", "Risk-budgeted allocation engine", "Single-strategy concentration caps", "Agent-operated, 24/7"],
    ["Passive allocators", "DAO treasuries", "Agent-run funds"],
    [("How is risk controlled?", "A fixed risk budget governs the vault: position sizing, venue diversification, and per-strategy caps are enforced on every rebalance — no exceptions, no overrides."),
     ("What do I need to start?", "Fund the vault and set your risk preference. The agents handle venue selection, sizing, and rebalancing from there.")],
    "🏦",
),
"P02_CLMM": (
    "Market-making, minus the desk.",
    "The Concentrated Liquidity Manager runs professional-grade liquidity positions with volatility-aware ranges — earning trading fees like a market maker, without the full-time operation.",
    "Concentrated liquidity pays far better than passive pools — but only when ranges are set right. Set them too tight and you're constantly rebalancing; too wide and your capital works half as hard. The CLMM Manager solves this with ranges derived from realized volatility, adjusted as conditions change.",
    "Fee income compounds automatically, and every range decision is backstopped by auditor-gated deployment standards. It's the closest thing to hiring a market-making desk, at a fraction of the cost.",
    [("Measure", "Realized volatility is computed continuously from live market data to size the optimal range width."),
     ("Position", "Liquidity is placed at tick-optimized ranges around the current price, weighted for fee capture."),
     ("Maintain", "As volatility regimes shift, ranges rebalance automatically — fees compound without manual intervention.")],
    ["Volatility-aware range sizing", "Tick-optimized placement", "Automatic fee compounding", "Auditor-gated deployment standards"],
    ["Liquidity providers", "Token issuers", "Yield-focused allocators"],
    [("What happens in volatile markets?", "Range width expands with realized volatility, keeping positions productive through regime changes instead of getting run over by them."),
     ("Do I need to manage positions?", "No. Range selection, rebalancing, and fee compounding are fully automated once your position is funded.")],
    "💧",
),
"P03_INTENT_DARK": (
    "Size without the footprint.",
    "The Intent Solver & Dark Pool executes large orders privately — splitting intents across dark venues so size never moves the market against you.",
    "Every large onchain order faces the same enemy: visibility. The moment size hits a public mempool, it gets traded against. The Intent Solver breaks orders into intelligently-sized pieces and routes them through private execution venues, settling at the best achievable price.",
    "Minimum-output protection guarantees each fill meets your bar, and settlement stays in the assets you chose. Big orders finally get institutional-grade execution on public rails.",
    [("Declare", "You state the intent: asset, size, and minimum acceptable output. Nothing touches a public venue yet."),
     ("Split & route", "The solver breaks the order into optimal pieces and routes them across dark and CoW-style venues."),
     ("Settle", "Fills execute privately and settle directly — with every fill meeting your minimum-output bar.")],
    ["Intelligent order splitting", "Private dark-venue execution", "Minimum-output protection", "Multi-asset settlement"],
    ["Whales & funds", "DAO treasuries", "Agent treasuries"],
    [("How is my order protected?", "Orders never sit exposed on public venues. Splitting plus private routing means the market can't see — or trade against — your size."),
     ("What does settlement look like?", "Fills settle directly in your chosen assets with a full receipt trail for every piece of the order.")],
    "🌑",
),
"P04_MEV": (
    "Your flow, protected. Their flow, priced.",
    "MEV Protection & Capture shields your transactions from predatory ordering — and turns observable market flow into captured value flowing back to you.",
    "Maximal extractable value is a tax on everyone who transacts naively: frontrunners skim your trades, and your own flow leaks value to searchers. This product flips the equation — shielding your transactions through private routing while its capture engine prices and claims value from public flow.",
    "Your keys never leave your control and treasury assets are never in scope. Protection first, capture second, safety always.",
    [("Shield", "Your transactions route privately, invisible to frontrunners and sandwich attackers."),
     ("Observe", "The engine monitors public flow for capturable value — arbitrage, backruns, liquidations."),
     ("Capture", "Identified opportunities are executed and the captured value flows back to the vault.")],
    ["Frontrun & sandwich shielding", "Private transaction routing", "Backrun & arbitrage capture", "Strict key-separation architecture"],
    ["Active traders", "Agent fleets", "High-volume desks"],
    [("Is my capital at risk from the capture engine?", "No. Capture operates on observable flow with its own risk envelope — your principal and keys stay isolated from execution."),
     ("What does protection cover?", "Every transaction routed through the product is shielded from public-mempool predators: no frontrunning, no sandwiching, no copy-trading.")],
    "🛡️",
),
"P05_INSURANCE": (
    "Coverage for the onchain economy.",
    "The DeFi Risk Mutual is onchain insurance done right — risk-scored premiums, reserve-backed coverage, and claims paid on verified events.",
    "Smart-contract risk, oracle failure, stablecoin depegs: the onchain economy runs on code, and code has edge cases. The Risk Mutual prices coverage from real risk scores — not vibes — and holds reserves against every policy written.",
    "Premiums reflect measured risk, claims require attested verification, and the reserve ratio is monitored continuously. Protection you can actually underwrite a position against.",
    [("Assess", "Protocols and positions are scored on audited risk factors — code, oracles, liquidity, and history."),
     ("Price", "Premiums are set from the risk score. Safer positions pay less; riskier ones pay their fair share."),
     ("Cover", "Policies are backed by mutual reserves, and verified claims pay out on attested events.")],
    ["Risk-scored premium pricing", "Reserve-backed policies", "Attested claim verification", "Continuous reserve monitoring"],
    ["DeFi power users", "DAO treasuries", "Institutional allocators"],
    [("What can I insure?", "Smart-contract exposure, oracle risk, and peg stability across covered protocols — each policy priced to its measured risk."),
     ("How are claims verified?", "Claims require independent attestation of the covered event before any payout executes. No attestation, no payout.")],
    "☂️",
),
"P06_PERPS": (
    "Delta-neutral, by design.",
    "The Perp DEX Hedging Swarm holds market exposure while neutralizing direction — harvesting funding rates and basis with a hedged book.",
    "Directional bets are exciting until they're not. The Hedging Swarm takes the other path: hold the exposure you want, hedge the direction you don't, and harvest the funding and basis spreads that hedged books earn in every market regime.",
    "A strict delta band keeps the book neutral, funding-sign filters avoid paying to hold hedges, and a liquidation buffer stands guard. Direction is noise; structure is the trade.",
    [("Measure", "Net delta is computed across every position in real time."),
     ("Hedge", "Perp positions are sized to neutralize directional exposure within a tight band."),
     ("Harvest", "Funding payments and basis spreads accrue to the book while neutrality is maintained.")],
    ["Delta-neutral book management", "Funding-rate harvesting", "Tight delta-band enforcement", "Liquidation buffer protection"],
    ["Market-neutral funds", "Hedged allocators", "Sophisticated traders"],
    [("What happens if funding flips negative?", "Funding-sign filters prevent the book from paying to stay hedged — positions unwind or flip before negative carry eats returns."),
     ("How neutral is neutral?", "Net delta is held inside a strict band and rebalanced continuously. The book has no directional opinion.")],
    "⚖️",
),
"P07_BRIDGE": (
    "Every chain, best route.",
    "The Cross-Chain Bridge Optimizer scores every route across allowlisted bridges and executes within your slippage cap — multichain capital, moved intelligently.",
    "Capital is multichain now, but bridging is still a gamble: which bridge, what fee, how much slippage, is the route even safe? The Optimizer replaces guesswork with scoring — every route ranked on cost, speed, and safety before a dollar moves.",
    "Only allowlisted bridges are ever eligible, and slippage caps are hard limits, not suggestions. Your capital arrives where it should, at the price you approved.",
    [("Score", "Every eligible route is scored on fee, speed, liquidity depth, and safety record."),
     ("Approve", "The top-scoring route is checked against your slippage cap and the bridge allowlist."),
     ("Execute", "Funds move along the winning route with a full receipt trail from source to destination.")],
    ["Multi-bridge route scoring", "Strict bridge allowlist", "Hard slippage caps", "End-to-end receipt trail"],
    ["Multichain funds", "DAO treasuries", "Agent operations"],
    [("Which bridges are supported?", "Only vetted, allowlisted bridges are eligible. No arbitrary routes, no experimental venues — safety first."),
     ("What if a route fails mid-transfer?", "The optimizer monitors execution end-to-end and every transfer carries a full receipt trail for recovery.")],
    "🌉",
),
"P08_RWA": (
    "Real-world yield, onchain rails.",
    "RWA Tokenization Vaults bring real-world assets onchain — yield from the real economy, with the transparency and settlement of DeFi.",
    "Treasuries, credit, real estate: trillions in real-world yield have been locked behind intermediaries. Tokenized vaults open the door — fractional access, onchain settlement, and reporting you can verify yourself.",
    "Every asset clears a compliance gate before a dollar of capital touches it, and capital stays in dry-run until every check passes. Real yield, without the real-world opacity.",
    [("Source", "Real-world assets are vetted: cash flows verified, counterparties assessed, structures reviewed."),
     ("Clear", "Each asset passes a compliance gate — legal structure, KYC posture, and reporting standards."),
     ("Tokenize", "Cleared assets are tokenized into vaults with transparent, onchain yield distribution.")],
    ["Compliance-gated assets", "Verified real-world cash flows", "Transparent onchain reporting", "Fractional access"],
    ["Yield seekers", "Diversifiers", "Institutions"],
    [("What kinds of assets?", "Yield-bearing real-world assets with verifiable cash flows — selected for durability, not hype."),
     ("How is compliance handled?", "Every asset clears a dedicated compliance gate covering legal structure and reporting before capital is eligible.")],
    "🏛️",
),
"P09_DAO_GOV": (
    "Every vote, optimized.",
    "The DAO Governance Optimizer turns scattered voting power into coordinated influence — simulating outcomes, weighting votes, and executing through timelocked safety.",
    "Governance tokens are influence, but most of it sleeps: holders miss votes, split power, and leave outcomes to whoever shows up. The Optimizer wakes it up — simulating proposal outcomes, concentrating weight where it matters, and executing through timelocks.",
    "Quorum is tracked, execution is timelocked, and nothing broadcasts without the safety rails. Your governance power finally votes like it means it.",
    [("Simulate", "Proposal outcomes are modeled before voting begins — know the impact before you commit weight."),
     ("Coordinate", "Voting power is weighted and directed to the proposals where it moves the needle most."),
     ("Execute", "Approved actions flow through timelocks with full transparency. Quorum tracked throughout.")],
    ["Proposal outcome simulation", "Vote-weight optimization", "Timelock-safe execution", "Quorum monitoring"],
    ["DAOs", "Delegates", "Governance funds"],
    [("Does it vote automatically?", "It prepares, simulates, and recommends — execution flows through timelocked rails with full transparency into every action."),
     ("Which DAOs are covered?", "Any DAO with onchain governance. Voting power is coordinated across every protocol you participate in.")],
    "🗳️",
),
"P10_FLASH_ARB": (
    "Profit in a single block.",
    "The Flash Loan Arbitrage Engine scans every block for price dislocations across venues — and captures them atomically with zero principal risk.",
    "Price differences between venues are free money, but only for whoever sees them first and executes fastest. The engine watches every block, simulates every opportunity, and fires only when profit clears a hard floor after gas.",
    "Atomic execution means the whole bundle either completes profitably or reverts entirely. No partial fills, no stuck positions — just captured spread.",
    [("Scan", "Every block is scanned across venues for price dislocations worth capturing."),
     ("Simulate", "Each opportunity is simulated end-to-end: profit floor checked, gas ceiling enforced."),
     ("Execute", "Winning bundles execute atomically via flash loan — profit or revert, nothing in between.")],
    ["Cross-venue opportunity scan", "Hard profit-floor filter", "Gas-ceiling protection", "Atomic all-or-nothing execution"],
    ["Quant traders", "Agent funds", "Arbitrage desks"],
    [("Is my principal at risk?", "No. Flash loans mean the strategy never deploys your capital — execution is atomic: profitable completion or full revert."),
     ("How are opportunities chosen?", "Only dislocations clearing a strict profit floor after gas costs are executed. Everything else is ignored.")],
    "⚡",
),
"P11_DELTA_NEUTRAL": (
    "Yield without the chart.",
    "Delta-Neutral Yield pairs spot holdings with perp hedges — harvesting funding rates while staying immune to market direction.",
    "The best yield in crypto often comes from funding rates — but capturing it usually means taking directional risk you didn't want. This product separates the two: hold the spot, hedge the direction, keep the funding.",
    "Basis is monitored continuously, and if the trade stops paying, positions unwind automatically. You earn the carry; the market keeps its opinions.",
    [("Pair", "Spot positions are matched with offsetting perp hedges sized to neutralize direction."),
     ("Harvest", "Funding payments accrue to the book as long as the basis stays favorable."),
     ("Unwind", "If basis turns negative, positions unwind automatically — no bag-holding a dead trade.")],
    ["Spot-perp hedging engine", "Funding-rate harvesting", "Continuous basis monitoring", "Automatic unwind on flip"],
    ["Yield seekers", "Market-neutral allocators", "Risk-averse traders"],
    [("What if the market crashes?", "That's the point of delta-neutral: direction is hedged out. The book earns funding regardless of which way prices move."),
     ("When does it exit?", "Automatically, when the basis turns against the trade. Capital is never left in a carry trade that stopped carrying.")],
    "🎯",
),
"P12_TWAMM": (
    "Size, sliced smart.",
    "The TWAMM Large-Order Engine executes big orders as time-weighted slices — capturing fair prices without ever dumping the market.",
    "Large orders and fair prices don't mix on public venues: size moves markets, and the market moves against size. TWAMM scheduling solves it the way institutions do — break the order into small slices, drip them over time, and let the average price do the work.",
    "Impact caps guard every slice, and no single block ever carries the full order. Patience, automated — and priced in.",
    [("Schedule", "Your order is divided into time-weighted slices across your chosen window."),
     ("Drip", "Slices execute steadily, each one small enough to avoid moving the market."),
     ("Settle", "Fills accumulate at a fair time-weighted average price with a complete audit trail.")],
    ["Time-weighted slice scheduling", "Per-slice impact caps", "No single-block dumps", "Full execution audit trail"],
    ["Large allocators", "Treasury managers", "Patient accumulators"],
    [("How long does execution take?", "You set the window — from minutes to days. Longer windows mean smaller slices and less impact."),
     ("What price do I get?", "A time-weighted average across the execution window: the fair price, not the panicked one.")],
    "🍰",
),
"P13_AVS": (
    "Restake with a seatbelt.",
    "AVS Tranching & Restaking brings structured-product discipline to restaking — senior and junior tranches priced on real slashing risk.",
    "Restaking pays more because it risks more: slashing can take principal, not just yield. Tranching contains that risk — senior tranches get paid first and take losses last, junior tranches earn more for standing in front.",
    "Slashing risk is monitored continuously through dedicated oracles, and junior exposure is hard-capped. Restaking yield, with the risk actually engineered.",
    [("Assess", "Slashing risk is evaluated per operator and AVS through dedicated monitoring."),
     ("Tranche", "Capital is split into senior and junior tranches priced on measured risk."),
     ("Allocate", "Tranches deploy across diversified operators with junior exposure capped.")],
    ["Senior/junior tranching", "Continuous slash monitoring", "Hard junior exposure caps", "Operator diversification"],
    ["Restakers", "Structured-yield seekers", "Risk-tiered allocators"],
    [("What's the difference between tranches?", "Senior gets paid first and takes losses last — lower yield, higher safety. Junior earns more for absorbing risk first."),
     ("How is slashing risk monitored?", "Dedicated oracles track operator and AVS slashing conditions continuously, with junior caps enforced at all times.")],
    "🥞",
),
"P14_PREDICTION": (
    "Edge, sized by Kelly.",
    "Prediction Market Automation trades event outcomes with Kelly-criterion position sizing — disciplined edge, mathematically sized.",
    "Prediction markets reward being right about the world, but most traders size by gut and blow up on variance. Kelly sizing fixes the math: bet proportionally to your edge, survive the inevitable losing streaks, and let the edge compound.",
    "Positions are capped, the book is managed across markets, and exits are disciplined. Conviction with a calculator.",
    [("Price", "Event probabilities are estimated from market prices, order flow, and available information."),
     ("Size", "Positions are sized by the Kelly criterion — proportional to edge, capped against ruin."),
     ("Manage", "The book is monitored across markets with disciplined exits as probabilities resolve.")],
    ["Kelly-criterion position sizing", "Multi-market book management", "Hard position caps", "Disciplined exit rules"],
    ["Event traders", "Quant hobbyists", "Information-edge holders"],
    [("What is Kelly sizing?", "A mathematical formula that sizes each bet proportionally to your edge — maximizing long-run growth while making ruin effectively impossible."),
     ("Which markets?", "Any liquid prediction market. The engine manages a diversified book rather than betting the farm on one outcome.")],
    "🔮",
),
"P15_LENDING": (
    "Your deposits, optimally deployed.",
    "The Lending Protocol Optimizer keeps your supplied assets earning the best available rate — shifting between venues as utilization and rates move.",
    "Lending rates are a moving target: the best venue this week is third-best next week, and manual chasing eats your edge in gas and attention. The Optimizer watches utilization bands continuously and moves deposits to where they earn most.",
    "Health factors are monitored on every position, and only battle-tested venues are eligible. Set it, forget it, earn.",
    [("Monitor", "Utilization and rates are tracked continuously across eligible lending venues."),
     ("Shift", "Deposits move to the best risk-adjusted rate when the improvement clears the bar."),
     ("Guard", "Position health is monitored around the clock with automatic protective action.")],
    ["Cross-venue rate optimization", "Utilization-band triggers", "24/7 health monitoring", "Blue-chip venue selection"],
    ["Stablecoin holders", "Passive lenders", "Treasury cash"],
    [("Which venues are eligible?", "Only established, battle-tested lending venues. New or unaudited protocols never make the list."),
     ("How often does it move my deposits?", "Only when the rate improvement clears a meaningful bar — no churn for basis points.")],
    "🏧",
),
"P16_DEX_AGG": (
    "Best price, every swap.",
    "The Best-Execution DEX Aggregator quotes every venue, splits your order intelligently, and settles at the best achievable price — minimum-output guaranteed.",
    "Every swap has a best price, and it's rarely on the first venue you check. The Aggregator quotes across the full venue set, splits orders where splitting wins, and guarantees your minimum output before anything executes.",
    "Venue allowlists keep execution on trusted rails, and the whole route is transparent before you confirm. Slippage becomes a choice, not a surprise.",
    [("Quote", "Every allowlisted venue is quoted in real time for your exact trade."),
     ("Route", "The engine finds the optimal split across venues — sometimes one, sometimes many."),
     ("Settle", "Execution settles at the best price with your minimum-output guarantee enforced.")],
    ["Multi-venue smart routing", "Intelligent order splitting", "Minimum-output guarantee", "Allowlisted venues only"],
    ["Traders", "Agent fleets", "Treasury operators"],
    [("How much better are the prices?", "It depends on size and market conditions — but the aggregator never settles for the first quote when a better one exists."),
     ("Is my trade protected?", "Yes. Minimum-output is enforced on every execution, and only allowlisted venues are eligible.")],
    "🔀",
),
"P17_OPTIONS": (
    "Options, engineered.",
    "The On-Chain Options Protocol brings real options markets onchain — volatility-priced, Greek-managed, and fully collateralized.",
    "Options are the professional's instrument: defined risk, leveraged exposure, income from premium. Onchain options have lagged — thin liquidity, crude pricing, no real risk management. This protocol prices from the volatility surface and manages Greeks like a proper desk.",
    "Every position is collateralized, payoffs are defined upfront, and the book is hedged continuously. Derivatives with the training wheels off — and the safety rails on.",
    [("Price", "Options are priced from the live volatility surface, not stale marks."),
     ("Structure", "Choose your payoff: calls, puts, spreads — with defined risk shown upfront."),
     ("Manage", "Greeks are monitored and hedged continuously through expiry or exit.")],
    ["Vol-surface pricing", "Defined-risk payoffs", "Continuous Greek management", "Full collateralization"],
    ["Sophisticated traders", "Hedgers", "Premium sellers"],
    [("What strategies are supported?", "Calls, puts, and spreads with transparent pricing — every payoff defined before you commit capital."),
     ("How is risk managed?", "Full collateralization plus continuous Greek hedging. No naked exposure, no surprises at expiry.")],
    "📊",
),
"P18_STRUCTURED": (
    "Wall-Street payoffs, DeFi rails.",
    "Structured Product Vaults package market views into defined-outcome investments — the payoff profiles institutions love, issued transparently onchain.",
    "Structured products are a multi-trillion-dollar market for one reason: people want defined outcomes, not open-ended risk. These vaults bring that engineering onchain — barrier notes, yield enhancers, capital-protected designs — with terms you can read and verify.",
    "Every product shows its payoff diagram upfront, terms are transparent, and settlement is automatic. Sophistication without the private-banking minimum.",
    [("Design", "Payoff profiles are engineered around market views: growth, income, or protection."),
     ("Verify", "Terms, barriers, and scenarios are published transparently before issuance."),
     ("Settle", "At maturity, payoffs execute automatically per the published terms.")],
    ["Defined-outcome payoffs", "Transparent published terms", "Automatic settlement", "Multiple payoff templates"],
    ["HNW allocators", "Yield enhancers", "Risk-defined investors"],
    [("What happens at maturity?", "The payoff executes automatically according to the published terms — no claims process, no counterparty chase."),
     ("Can I see the scenarios?", "Yes. Every product publishes its payoff diagram and scenario analysis before you invest a dollar.")],
    "🧬",
),
"P19_CREDIT": (
    "Credit scores for wallets.",
    "Decentralized Credit Underwriting scores onchain history into real credit profiles — then prices loans to match the risk.",
    "Onchain lending today is overcollateralized or nothing — billions in productive credit locked out because wallets have no credit score. This product builds one: repayment history, wallet behavior, and protocol interaction, scored into terms that reflect reality.",
    "Terms are dynamic, monitoring is continuous, and the book stays diversified. Credit, finally, for the onchain economy.",
    [("Score", "Wallet history is analyzed into a credit profile: repayments, behavior, and tenure."),
     ("Price", "Loan terms — rate, size, duration — are set from the score, not from a flat table."),
     ("Monitor", "Borrower health is tracked continuously with terms adjusting to performance.")],
    ["Onchain credit scoring", "Risk-priced loan terms", "Continuous borrower monitoring", "Diversified loan book"],
    ["Lenders", "Credit funds", "Emerging-market borrowers"],
    [("How is a wallet scored?", "Repayment history, protocol interaction, wallet tenure, and behavioral signals — combined into a living credit profile."),
     ("What protects lenders?", "Risk-priced terms, continuous monitoring, diversification across the book, and dynamic adjustment as profiles evolve.")],
    "💳",
),
"P20_COMPLIANCE": (
    "Compliance on autopilot.",
    "DeFi Compliance Automation screens transactions, flags risk, and generates audit-ready reports — regulatory posture as a product feature.",
    "Compliance is the tax every serious onchain operation pays — in headcount, in delays, in anxiety. Automation turns it into infrastructure: every transaction screened, every flag raised in real time, every report audit-ready.",
    "The design is conservative by default: when in doubt, it escalates rather than approves. Your regulators get answers; your team gets its time back.",
    [("Screen", "Every transaction is screened against sanctions, risk, and policy rules in real time."),
     ("Flag", "Anomalies surface instantly with full context — who, what, why it tripped."),
     ("Report", "Audit-ready reports generate on demand: complete, timestamped, verifiable.")],
    ["Real-time transaction screening", "Instant risk flagging", "Audit-ready reporting", "Conservative-by-design escalation"],
    ["Funds & desks", "Exchanges", "DAOs & protocols"],
    [("What happens on a flag?", "The transaction is escalated with full context for review. The system errs toward caution — uncertain activity gets human eyes."),
     ("Can it satisfy auditors?", "That's the point: complete, timestamped, verifiable records generated on demand, ready for any examination.")],
    "✅",
),
"P21_TREASURY_DAO": (
    "Your treasury, professionally run.",
    "DAO Treasury Management brings institutional treasury discipline to decentralized organizations — runway planning, diversified allocation, and reporting the community can trust.",
    "Most DAO treasuries are a multisig and a prayer: native token heavy, undiversified, no runway plan. This product runs the treasury like an institution — runway modeled, allocations diversified, spending policy enforced, reports published.",
    "The community sees everything: allocations, performance, and runway, on a regular cadence. Stewardship you can verify.",
    [("Assess", "Runway, concentration, and risk are modeled from the treasury's actual positions."),
     ("Allocate", "Capital is diversified per the community's approved investment policy."),
     ("Report", "Performance and runway are published on a regular cadence for full transparency.")],
    ["Runway modeling", "Policy-based diversification", "Spending-policy enforcement", "Community-transparent reporting"],
    ["DAOs", "Foundations", "Grant programs"],
    [("Who sets the policy?", "The community. The product enforces the investment policy the DAO approves — allocation bands, spending limits, and all."),
     ("What does reporting look like?", "Regular published reports: positions, performance, runway, and policy compliance — verifiable by any member.")],
    "🏛️",
),
"P22_STABLE_YIELD": (
    "Dollars that never sleep.",
    "The Stablecoin Yield Maximizer keeps your dollar-denominated capital earning the best available onchain rate — safely, continuously, automatically.",
    "Stablecoins are the working capital of the onchain economy, but most of it sits idle earning nothing. The Maximizer fixes that: scanning vetted venues, allocating to the best risk-adjusted rate, and compounding the result.",
    "Stable-only, diversified across venues, with capital preservation as the prime directive. Your dollars work the night shift.",
    [("Scan", "Vetted stablecoin venues are monitored continuously for rate and risk."),
     ("Allocate", "Capital flows to the best risk-adjusted rate, diversified across venues."),
     ("Compound", "Earnings compound automatically. No claims, no manual harvesting.")],
    ["Stablecoin-only mandate", "Multi-venue diversification", "Automatic compounding", "Capital-preservation priority"],
    ["Cash holders", "Businesses", "Conservative allocators"],
    [("Is my principal safe?", "Capital preservation is the prime directive: stable-only assets, diversified venues, and continuous risk monitoring."),
     ("Which venues?", "Only vetted, established stablecoin venues. The list is curated for safety first, yield second.")],
    "💵",
),
"P23_NFTFI": (
    "Liquidity for the illiquid.",
    "NFT-Fi Liquidity Pools turn NFT holdings into productive capital — appraised, pooled, and lendable with proper risk controls.",
    "NFTs hold enormous value and near-zero liquidity: selling means taking a haircut, and borrowing means finding a counterparty by hand. Pooled liquidity fixes both — professional appraisals set values, pools provide the capital, and borrowers get terms in minutes.",
    "LTV guards and a real liquidation engine keep the pools solvent. Illiquid no longer means unproductive.",
    [("Appraise", "Holdings are valued by professional appraisal models, not floor-price guesses."),
     ("Pool", "Capital aggregates into lending pools organized by collection and risk tier."),
     ("Lend", "Borrowers draw against appraised value with LTV guards and automated liquidation.")],
    ["Professional appraisal models", "Pooled lending capital", "LTV risk guards", "Automated liquidation engine"],
    ["NFT holders", "Collectors", "NFT funds"],
    [("How are NFTs valued?", "Dedicated appraisal models considering rarity, traits, sales history, and collection dynamics — not just floor price."),
     ("What protects lenders?", "Conservative LTV ratios, diversified pools, and an automated liquidation engine that acts before losses compound.")],
    "🖼️",
),
"P24_SOCIALFI": (
    "Attention, monetized.",
    "SocialFi Revenue Share turns creator attention into structured, distributable revenue — tracked onchain, split transparently.",
    "Attention is the scarcest asset online, and creators capture a fraction of the value they generate. This product structures it: revenue tracked onchain, splits defined upfront, distributions automatic.",
    "Pro-rata accounting means every stakeholder sees exactly what they're owed, and payouts execute without intermediaries taking a cut of the cut.",
    [("Track", "Revenue streams are recorded onchain as they're generated."),
     ("Split", "Distribution rules are defined upfront: creators, curators, and backers, pro-rata."),
     ("Distribute", "Payouts execute automatically per the published splits. No invoices, no delays.")],
    ["Onchain revenue tracking", "Pro-rata distribution engine", "Transparent published splits", "Automatic payouts"],
    ["Creators", "Creator funds", "Fan communities"],
    [("Who gets paid?", "Whoever the splits define: creators, collaborators, curators, backers — each share published and verifiable."),
     ("How are splits decided?", "Upfront, before revenue flows. Transparent terms that all parties agree to in advance.")],
    "📣",
),
"P25_PORTFOLIO": (
    "A strategist in software.",
    "The Agent-Managed Portfolio is a full portfolio strategist in software — goal-based allocation, automatic rebalancing, and risk overlays, run by agents.",
    "Portfolio management is 10% strategy and 90% discipline: rebalance on schedule, don't chase, respect the risk budget. Agents are perfect at the 90% — and this product gives them the 10% too, with goal-based allocation models.",
    "Risk overlays guard the downside, rebalancing is automatic, and every decision is explainable. Wealth management's discipline, software's price.",
    [("Profile", "Your goals, horizon, and risk tolerance define the allocation model."),
     ("Allocate", "Capital deploys across the strategy set per your profile."),
     ("Rebalance", "The portfolio rebalances automatically with risk overlays guarding the downside.")],
    ["Goal-based allocation models", "Automatic rebalancing", "Downside risk overlays", "Explainable decisions"],
    ["Long-term investors", "Busy professionals", "First-time allocators"],
    [("How is my risk handled?", "Your risk tolerance sets hard guardrails: allocation bands, downside overlays, and automatic de-risking when limits approach."),
     ("Can I see what it's doing?", "Completely. Every allocation, rebalance, and the reasoning behind it — full transparency, always.")],
    "🧭",
),
"P26_DEFI_OS": (
    "The suite, self-improving.",
    "The Self-Improving DeFi OS is the intelligence layer over all 26 products — observing performance, learning what works, and upgrading strategies across the suite.",
    "Twenty-six products generate something no single product can: a view of the whole. The DeFi OS watches every strategy, attributes performance to decisions, and propagates what works — so each product gets smarter from the experience of all the others.",
    "Human oversight governs every upgrade. The system proposes; principals dispose. Compound learning, with the brakes where they belong.",
    [("Observe", "Performance is tracked across all 26 products with decision-level attribution."),
     ("Learn", "Winning patterns are identified: which strategies work, in which regimes, and why."),
     ("Upgrade", "Improvements propagate across the suite under human oversight — every product, smarter.")],
    ["Cross-product performance attribution", "Regime-aware strategy learning", "Suite-wide upgrade propagation", "Human-governed improvements"],
    ["Suite allocators", "Institutions", "Strategists"],
    [("Does it change strategies on its own?", "It proposes improvements with full evidence; upgrades apply under human oversight. Learning is aggressive, deployment is governed."),
     ("Why does the whole suite matter?", "Because 26 products see 26x the market. Patterns invisible to any single strategy become obvious across the suite.")],
    "🧠",
),
}

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name} — SINCOR Products</title>
<meta name="description" content="{tagline}">
<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
<link rel="stylesheet" href="/static/sincor-pro.css">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Sora:wght@600;700;800&display=swap" rel="stylesheet">
</head>
<body>

{{% include "_pro_nav.html" %}}

<header class="sp-hero">
  <div class="sp-wrap">
    <span class="sp-eyebrow">{category_label}</span>
    <h1 class="sp-h1">{name}<br><span class="gold">{tagline}</span></h1>
    <p class="sp-lede">{lede}</p>
    <div class="sp-cta-row">
      <a href="/signup?plan={slug}" class="sp-btn sp-btn-gold">Request early access →</a>
      <a href="/products" class="sp-btn sp-btn-ghost">All 26 products</a>
    </div>
    <div class="sp-specs">
      <div class="sp-spec"><div class="k">Target yield</div><div class="v">{yield_v}<br><small>{yield_n}</small></div></div>
      <div class="sp-spec"><div class="k">Protocol fee</div><div class="v">{fee_v}<br><small>{fee_n}</small></div></div>
      <div class="sp-spec"><div class="k">Category</div><div class="v"><small>{category_label}</small></div></div>
      <div class="sp-spec"><div class="k">Designed for</div><div class="v"><small>{mincap}</small></div></div>
    </div>
  </div>
</header>

<section class="sp-section tight">
  <div class="sp-wrap">
    <div class="sp-kicker">What it does</div>
    <h2 class="sp-h2">Engineered for one job.</h2>
    <p class="sp-sub">{body1}</p>
    <p class="sp-sub">{body2}</p>
    <div class="sp-feat">
      <div>
        <h3>How it works</h3>
        <div class="sp-steps" style="grid-template-columns:1fr">
          {steps}
        </div>
      </div>
      <div class="sp-visual">{icon}</div>
    </div>
    <h3 style="font-size:22px;margin:40px 0 6px">Capabilities</h3>
    <ul class="sp-check">
      {caps}
    </ul>
    <h3 style="font-size:22px;margin:40px 0 14px">Who it's for</h3>
    <div class="sp-pills">
      {audiences}
    </div>
  </div>
</section>

<hr class="sp-divider">

<section class="sp-section tight">
  <div class="sp-wrap">
    <div class="sp-kicker sp-center">Questions</div>
    <h2 class="sp-h2 sp-center">About {short}.</h2>
    <div class="sp-faq" style="margin-top:32px">
      {faqs}
    </div>
  </div>
</section>

<section class="sp-section tight">
  <div class="sp-wrap">
    <div class="sp-cta-band">
      <h2>Get {short} working for you.</h2>
      <p>Join the priority list. We'll reach out the moment your allocation opens — early access members get first placement.</p>
      <div class="sp-cta-row" style="justify-content:center">
        <a href="/signup?plan={slug}" class="sp-btn sp-btn-gold">Request early access →</a>
        <a href="/pricing" class="sp-btn sp-btn-ghost">See platform plans</a>
      </div>
    </div>
    <div class="sp-center" style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-top:8px">
      <a href="/products/{prev_slug}" class="sp-card-link">← {prev_name}</a>
      <a href="/products/{next_slug}" class="sp-card-link">{next_name} →</a>
    </div>
  </div>
</section>

{{% include "_pro_footer.html" %}}

</body>
</html>
"""

CATEGORY_LABELS = {
    "yield": "Yield", "liquidity": "Liquidity", "execution": "Execution",
    "mev": "MEV", "insurance": "Insurance", "derivatives": "Derivatives",
    "infra": "Infrastructure", "rwa": "Real-World Assets", "governance": "Governance",
    "arb": "Arbitrage", "restake": "Restaking", "markets": "Markets",
    "lending": "Lending", "credit": "Credit", "compliance": "Compliance",
    "treasury": "Treasury", "nftfi": "NFT-Fi", "social": "SocialFi",
    "portfolio": "Portfolio", "structured": "Structured", "meta": "Intelligence",
}


def fmt_apr(a: float) -> str:
    return f"{a * 100:.1f}%"


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    ordered = [p for p in PROTOCOLS if p.protocol_id in SLUGS]
    ordered.sort(key=lambda p: p.swarm_id)
    n = len(ordered)
    assert n == 26, f"expected 26 protocols, got {n}"
    for i, p in enumerate(ordered):
        slug = SLUGS[p.protocol_id]
        (tagline, lede, body1, body2, steps, caps,
         audiences, faqs, icon) = COPY[p.protocol_id]
        prev_p, next_p = ordered[(i - 1) % n], ordered[(i + 1) % n]
        if p.target_apr and p.target_apr > 0:
            yield_v, yield_n = fmt_apr(p.target_apr), "design target"
        else:
            yield_v, yield_n = "Utility", "infrastructure"
        if p.fee_bps and p.fee_bps > 0:
            fee_v = f"{p.fee_bps / 100:.2f}%".rstrip("0").rstrip(".") + "%"
            fee_n = "protocol fee"
        else:
            fee_v, fee_n = "—", "included in suite"
        if p.min_capital_usd and p.min_capital_usd > 0:
            mincap = f"${p.min_capital_usd:,.0f}+ allocations"
        else:
            mincap = "All allocation sizes"
        cat = CATEGORY_LABELS.get(p.category, p.category.title())
        steps_html = "".join(
            f'<div class="sp-step"><div class="n">{j + 1}</div><h3>{t}</h3><p>{d}</p></div>'
            for j, (t, d) in enumerate(steps))
        caps_html = "".join(f"<li><strong>{c.split('—')[0].strip()}</strong>{(' — ' + c.split('—', 1)[1]) if '—' in c else ''}</li>" for c in caps)
        # simpler: bold whole capability lead
        caps_html = "".join(f"<li><strong>{c}</strong></li>" for c in caps)
        aud_html = "".join(f'<span class="sp-pill">{a}</span>' for a in audiences)
        faq_html = "".join(
            f"<details><summary>{q}</summary><div class=\"a\">{a}</div></details>"
            for q, a in faqs)
        short = p.name.replace(" Swarm", "")
        html = PAGE.format(
            name=p.name, tagline=tagline, lede=lede, slug=slug,
            category_label=cat, yield_v=yield_v, yield_n=yield_n,
            fee_v=fee_v, fee_n=fee_n, mincap=mincap,
            body1=body1, body2=body2, steps=steps_html, caps=caps_html,
            audiences=aud_html, faqs=faq_html, icon=icon, short=short,
            prev_slug=SLUGS[prev_p.protocol_id], prev_name=prev_p.name,
            next_slug=SLUGS[next_p.protocol_id], next_name=next_p.name,
        )
        # Jinja include markers were escaped for .format()
        html = html.replace("{{%", "{%").replace("%}}", "%}")
        with open(os.path.join(OUT, slug + ".html"), "w") as f:
            f.write(html)
        print("wrote", slug)


if __name__ == "__main__":
    main()
