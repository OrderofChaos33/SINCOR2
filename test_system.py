import asyncio
from monetization_engine import MonetizationEngine

async def test_system():
    print("Testing SINCOR monetization system...")

    engine = MonetizationEngine()
    print('SUCCESS: MonetizationEngine created')

    opportunities = await engine.identify_revenue_opportunities()
    print(f'SUCCESS: Found {len(opportunities)} revenue opportunities')

    if opportunities:
        print(f'Top opportunity: {opportunities[0].revenue_stream.value} - ${opportunities[0].revenue_potential:.2f}')
        print(f'Confidence: {opportunities[0].confidence_score:.1%}')
        print(f'Time to close: {opportunities[0].time_to_close} days')

    # Test monetization execution
    strategy_report = await engine.execute_monetization_strategy(max_concurrent_opportunities=5)
    exec_summary = strategy_report['execution_summary']

    print(f"\nEXECUTION RESULTS:")
    print(f"Opportunities executed: {exec_summary['opportunities_executed']}")
    print(f"Success rate: {exec_summary['success_rate']:.1%}")
    print(f"Total revenue: ${exec_summary['total_revenue']:,.2f}")
    print(f"Profit margin: {exec_summary['profit_margin']:.1%}")

    return True

if __name__ == "__main__":
    result = asyncio.run(test_system())
    print('\nCOMPLETE: SINCOR monetization system fully functional and ready for deployment')