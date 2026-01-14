import yaml
import asyncpg
from typing import List, Dict, Optional

class AuctionEngine:
    def __init__(self, config_path: str, db_conn):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)
        self.db = db_conn
    
    async def get_buyer_reputation(self, buyer_id: str) -> int:
        """Get buyer reputation score from database"""
        row = await self.db.fetchrow("SELECT score FROM buyer_reputation WHERE buyer_id = $1", buyer_id)
        return row['score'] if row else 80  # default reputation
    
    def calculate_composite_score(self, buyer: Dict, reputation: int, destination_config: Dict) -> float:
        """Calculate weighted composite score for auction ranking"""
        price_weight = destination_config.get('price_weight', 0.5)
        quality_weight = destination_config.get('quality_weight', 0.3)
        reputation_weight = destination_config.get('reputation_weight', 0.2)
        
        # Normalize scores (0-100 scale)
        price_score = min(100, buyer.get('min_price', 0))  # Higher price = higher score
        quality_score = buyer.get('quality', 80)  # Default quality
        reputation_score = reputation
        
        composite = (
            (price_score * price_weight) +
            (quality_score * quality_weight) + 
            (reputation_score * reputation_weight)
        )
        
        return composite
    
    async def run_auction(self, lead: Dict, destination: str) -> Optional[Dict]:
        """Run auction for a lead and return winning buyer"""
        if destination not in self.config['destinations']:
            return None
            
        dest_config = self.config['destinations'][destination]
        
        if dest_config.get('mode') != 'auction':
            # Not an auction destination, use first eligible buyer
            eligible_buyers = [b for b in self.config['buyers'] if destination in b.get('categories', [])]
            return eligible_buyers[0] if eligible_buyers else None
        
        # Auction mode - find all eligible buyers and rank them
        eligible_buyers = []
        min_price = dest_config.get('min_price', 0)
        
        for buyer in self.config['buyers']:
            if destination in buyer.get('categories', []):
                if buyer.get('min_price', 0) >= min_price:
                    reputation = await self.get_buyer_reputation(buyer['id'])
                    score = self.calculate_composite_score(buyer, reputation, dest_config)
                    eligible_buyers.append({
                        **buyer,
                        'reputation': reputation,
                        'composite_score': score
                    })
        
        if not eligible_buyers:
            return None
            
        # Sort by composite score (highest wins)
        eligible_buyers.sort(key=lambda x: x['composite_score'], reverse=True)
        winner = eligible_buyers[0]
        
        return {
            'buyer_id': winner['id'],
            'buyer_name': winner['name'],
            'winning_bid': winner['min_price'],
            'webhook': winner['webhook'],
            'composite_score': winner['composite_score'],
            'auction_metadata': {
                'total_bidders': len(eligible_buyers),
                'min_floor': min_price,
                'winning_score': winner['composite_score']
            }
        }
    
    async def update_buyer_reputation(self, buyer_id: str, outcome: str, callback_time: int = None):
        """Update buyer reputation based on lead outcome"""
        if outcome == 'returned':
            # Decrease reputation for returns
            await self.db.execute("""
                INSERT INTO buyer_reputation (buyer_id, returns, total_leads) 
                VALUES ($1, 1, 1)
                ON CONFLICT (buyer_id) 
                DO UPDATE SET 
                    returns = buyer_reputation.returns + 1,
                    total_leads = buyer_reputation.total_leads + 1,
                    score = GREATEST(20, buyer_reputation.score - 2),
                    updated_at = now()
            """, buyer_id)
        elif outcome == 'delivered':
            # Increase reputation for successful deliveries
            await self.db.execute("""
                INSERT INTO buyer_reputation (buyer_id, total_leads)
                VALUES ($1, 1) 
                ON CONFLICT (buyer_id)
                DO UPDATE SET
                    total_leads = buyer_reputation.total_leads + 1,
                    score = LEAST(100, buyer_reputation.score + 1),
                    updated_at = now()
            """, buyer_id)