import numpy as np

# Constants
INITIAL_INVESTMENT = 10000  # $10k
SINC_PRICE_INITIAL = 0.01   # Assume $0.01 per SINC
TOTAL_SUPPLY = 1_000_000_000  # 1B tokens
BURN_RATE = 0.01  # 1% burn per transaction
INFLATION_RATE = 0.02  # 2% annual inflation for staking
STAKING_APY = 0.10  # 10% base APY
TRANSACTION_FEE = 0.005  # 0.5% fee
DAILY_TRANSACTIONS = 10000  # Assume 10k daily transactions
DAYS_IN_YEAR = 365

# Scenarios: Bull (price increases 200%), Chop (flat), Bear (price decreases 50%)
scenarios = {
    'Bull': {'price_multiplier': 2.0, 'transaction_growth': 1.5},
    'Chop': {'price_multiplier': 1.0, 'transaction_growth': 1.0},
    'Bear': {'price_multiplier': 0.5, 'transaction_growth': 0.5}
}

def simulate_year(scenario):
    price = SINC_PRICE_INITIAL
    staked_tokens = INITIAL_INVESTMENT / price  # Initial stake
    total_supply = TOTAL_SUPPLY
    velocity = 0  # Token velocity (transactions per token)
    roi = 0
    
    for day in range(DAYS_IN_YEAR):
        # Price evolution
        price *= (1 + (scenario['price_multiplier'] - 1) / DAYS_IN_YEAR)
        
        # Transaction volume
        tx_volume = DAILY_TRANSACTIONS * (scenario['transaction_growth'] ** (day / DAYS_IN_YEAR))
        
        # Burns and fees
        burn_amount = tx_volume * BURN_RATE
        fee_amount = tx_volume * TRANSACTION_FEE
        total_supply -= burn_amount
        
        # Inflation for staking rewards
        inflation_amount = total_supply * (INFLATION_RATE / DAYS_IN_YEAR)
        total_supply += inflation_amount
        
        # Staking rewards
        daily_reward = staked_tokens * (STAKING_APY / DAYS_IN_YEAR)
        staked_tokens += daily_reward
        
        # Velocity: transactions / circulating supply
        circulating_supply = total_supply - staked_tokens  # Simplified
        velocity += tx_volume / circulating_supply if circulating_supply > 0 else 0
        
        # ROI calculation
        portfolio_value = staked_tokens * price
        roi = (portfolio_value - INITIAL_INVESTMENT) / INITIAL_INVESTMENT
    
    avg_velocity = velocity / DAYS_IN_YEAR
    return roi, avg_velocity, total_supply

# Run simulations
results = {}
for name, params in scenarios.items():
    roi, velocity, final_supply = simulate_year(params)
    results[name] = {'ROI': roi, 'Velocity': velocity, 'Final_Supply': final_supply}
    print(f"{name} Market: ROI={roi:.2%}, Avg Velocity={velocity:.4f}, Final Supply={final_supply:.0f}")

# Target: 10-20% annual ROI
for name, res in results.items():
    if 0.10 <= res['ROI'] <= 0.20:
        print(f"{name}: PASS (ROI in target range)")
    else:
        print(f"{name}: FAIL (ROI {res['ROI']:.2%} not in 10-20%)")