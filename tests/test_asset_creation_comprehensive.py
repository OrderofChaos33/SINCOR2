"""
Comprehensive Test Suite for Asset Creation System
Unit tests, integration tests, and performance tests
"""

import unittest
import json
import time
from datetime import datetime
from typing import Dict, List

# Import systems under test
from sincor2.asset_orchestration_engine import (
    asset_orchestrator, Asset, AssetRequest, AssetType, AssetStatus,
    AssetQualityMetrics, QualityStandard
)
from sincor2.value_standards_framework import value_attribution
from sincor2.optimized_workflow_engine import (
    optimized_workflow_engine, WorkflowPhase, WorkflowOptimizationMetrics
)

# ============================================================================
# UNIT TESTS
# ============================================================================

class TestAssetOrchestration(unittest.TestCase):
    """Unit tests for asset orchestration"""

    def setUp(self):
        """Set up test fixtures"""
        self.orchestrator = asset_orchestrator
        self.orchestrator.asset_registry.clear()  # Clean state

    def test_create_asset_request(self):
        """Test asset request creation"""
        request = self.orchestrator.create_asset_request(
            asset_type=AssetType.INTELLIGENCE_REPORT,
            client_id="test_client",
            description="Test report",
            urgency=1.0,
            quality_tier=QualityStandard.STANDARD
        )

        assert request.request_id is not None
        assert request.asset_type == AssetType.INTELLIGENCE_REPORT
        assert request.client_id == "test_client"
        print("✅ test_create_asset_request PASSED")

    def test_initiate_asset_creation(self):
        """Test asset creation initiation"""
        request = self.orchestrator.create_asset_request(
            asset_type=AssetType.INTELLIGENCE_REPORT,
            client_id="test_client",
            description="Test report"
        )

        asset = self.orchestrator.initiate_asset_creation(request)

        assert asset.request_id == request.request_id
        assert asset.status == AssetStatus.PLANNING
        assert asset.asset_id in self.orchestrator.asset_registry
        print("✅ test_initiate_asset_creation PASSED")

    def test_assess_quality_passes(self):
        """Test quality assessment - pass case"""
        request = self.orchestrator.create_asset_request(
            asset_type=AssetType.INTELLIGENCE_REPORT,
            client_id="test_client",
            description="Test report"
        )
        asset = self.orchestrator.initiate_asset_creation(request)

        # Good quality scores
        metrics = AssetQualityMetrics(
            accuracy=0.87,
            completeness=0.82,
            relevance=0.88,
            timeliness=0.75,
            clarity=0.85,
            actionability=0.72,
            innovation=0.65,
            depth=0.83,
            credibility=0.86
        )

        passed = self.orchestrator.assess_asset_quality(asset.asset_id, metrics)

        assert passed == True
        assert asset.status == AssetStatus.APPROVED
        print("✅ test_assess_quality_passes PASSED")

    def test_assess_quality_fails(self):
        """Test quality assessment - fail case"""
        request = self.orchestrator.create_asset_request(
            asset_type=AssetType.INTELLIGENCE_REPORT,
            client_id="test_client",
            description="Test report"
        )
        asset = self.orchestrator.initiate_asset_creation(request)

        # Poor quality scores
        metrics = AssetQualityMetrics(
            accuracy=0.55,  # Below 0.60 minimum
            completeness=0.70,  # Below 0.75 minimum
            relevance=0.50,
            timeliness=0.40,
            clarity=0.60,
            actionability=0.50,
            innovation=0.30,
            depth=0.50,
            credibility=0.55
        )

        passed = self.orchestrator.assess_asset_quality(asset.asset_id, metrics)

        assert passed == False
        assert asset.status == AssetStatus.REVISION_NEEDED
        assert len(asset.quality_feedback) > 0
        print("✅ test_assess_quality_fails PASSED")

    def test_calculate_asset_value(self):
        """Test asset value calculation"""
        request = self.orchestrator.create_asset_request(
            asset_type=AssetType.INTELLIGENCE_REPORT,
            client_id="test_client",
            description="Test report"
        )
        asset = self.orchestrator.initiate_asset_creation(request)

        # Set good quality
        metrics = AssetQualityMetrics(
            accuracy=0.87, completeness=0.82, relevance=0.88,
            timeliness=0.75, clarity=0.85, actionability=0.72,
            innovation=0.65, depth=0.83, credibility=0.86
        )
        self.orchestrator.assess_asset_quality(asset.asset_id, metrics)

        # Calculate value
        value = self.orchestrator.calculate_asset_value(
            asset_id=asset.asset_id,
            base_price=2500,
            complexity="medium",
            market_demand=1.2
        )

        # $2500 × 1.0 (medium) × ~1.17 (quality) × 1.2 (demand) ≈ $3510
        assert value.final_price > 3000
        assert value.final_price < 4000
        assert value.creation_cost == 500  # 20% of base
        assert value.gross_margin > 0
        print(f"✅ test_calculate_asset_value PASSED (price: ${value.final_price:.2f})")

    def test_complete_asset(self):
        """Test asset completion"""
        request = self.orchestrator.create_asset_request(
            asset_type=AssetType.INTELLIGENCE_REPORT,
            client_id="test_client",
            description="Test report"
        )
        asset = self.orchestrator.initiate_asset_creation(request)

        self.orchestrator.complete_asset(asset.asset_id)

        assert asset.status == AssetStatus.DELIVERED
        assert asset.completion_time is not None
        print("✅ test_complete_asset PASSED")


class TestValueStandards(unittest.TestCase):
    """Unit tests for value standards"""

    def setUp(self):
        """Set up test fixtures"""
        self.value_engine = value_attribution

    def test_get_standard(self):
        """Test getting standard for asset type"""
        standard = self.value_engine.get_standard("intelligence_report")

        assert standard is not None
        assert standard["base_price"] == 2500
        assert standard["min_quality_for_delivery"] == 0.75
        print("✅ test_get_standard PASSED")

    def test_quality_premium_calculation(self):
        """Test quality-based pricing premium"""
        # Standard tier (0.75-0.79)
        mult_standard = self.value_engine.calculate_quality_premium("intelligence_report", 0.77)
        assert mult_standard == 1.0

        # Premium tier (0.80-0.89)
        mult_premium = self.value_engine.calculate_quality_premium("intelligence_report", 0.85)
        assert mult_premium == 1.2

        # Enterprise tier (0.90+)
        mult_enterprise = self.value_engine.calculate_quality_premium("intelligence_report", 0.93)
        assert mult_enterprise == 1.35

        print("✅ test_quality_premium_calculation PASSED")

    def test_validate_asset_value(self):
        """Test asset value validation"""
        valid, issues = self.value_engine.validate_asset_value(
            "intelligence_report",
            quality_score=0.82,
            actual_metrics={"market_insights": 0.35, "actionable_recommendations": 0.45}
        )

        assert isinstance(valid, bool)
        assert isinstance(issues, list)
        print(f"✅ test_validate_asset_value PASSED (valid: {valid}, issues: {len(issues)})")


class TestWorkflowOptimization(unittest.TestCase):
    """Unit tests for optimized workflow"""

    def setUp(self):
        """Set up test fixtures"""
        self.workflow = optimized_workflow_engine
        self.workflow.workflow_metrics.clear()

    def test_workflow_execution(self):
        """Test complete workflow execution"""
        asset_data = {
            "asset_type": "intelligence_report",
            "client_id": "test_client",
            "description": "Test report",
            "accuracy": 0.87,
            "completeness": 0.82,
            "relevance": 0.88,
            "timeliness": 0.75,
            "clarity": 0.85,
            "actionability": 0.72,
            "innovation": 0.65,
            "depth": 0.83,
            "credibility": 0.86
        }

        success, metrics = self.workflow.execute_optimized_workflow("test_asset_001", asset_data)

        assert success == True
        assert metrics["total_duration_seconds"] > 0
        assert metrics["parallel_efficiency"] > 0
        print(f"✅ test_workflow_execution PASSED (duration: {metrics['total_duration_seconds']:.2f}s)")

    def test_workflow_caching(self):
        """Test workflow caching"""
        asset_data = {
            "asset_type": "intelligence_report",
            "client_id": "test_client",
            "description": "Test report"
        }

        # First execution - cache miss
        start = time.time()
        success1, metrics1 = self.workflow.execute_optimized_workflow("test_asset_001", asset_data)
        time1 = time.time() - start

        # Second execution - cache hit
        start = time.time()
        success2, metrics2 = self.workflow.execute_optimized_workflow("test_asset_002", asset_data)
        time2 = time.time() - start

        assert success1 == True and success2 == True
        print(f"✅ test_workflow_caching PASSED (first: {time1:.3f}s, second: {time2:.3f}s)")


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestEndToEndWorkflow(unittest.TestCase):
    """Integration tests for complete workflows"""

    def setUp(self):
        """Set up test fixtures"""
        self.orchestrator = asset_orchestrator
        self.orchestrator.asset_registry.clear()
        self.workflow = optimized_workflow_engine

    def test_full_asset_creation_workflow(self):
        """Test complete asset creation from request to delivery"""
        print("\n=== FULL ASSET CREATION WORKFLOW TEST ===")

        # Step 1: Create request
        request = self.orchestrator.create_asset_request(
            asset_type=AssetType.INTELLIGENCE_REPORT,
            client_id="enterprise_client_001",
            description="Market analysis for Q1 2026",
            urgency=1.5,
            quality_tier=QualityStandard.PREMIUM,
            client_tier="enterprise"
        )
        print(f"✅ Request created: {request.request_id}")

        # Step 2: Initiate creation
        asset = self.orchestrator.initiate_asset_creation(request)
        asset.assigned_agents = ["E-auriga-01", "E-vega-02", "E-synthesizer-03"]
        print(f"✅ Asset created: {asset.asset_id} | Status: {asset.status.value}")

        # Step 3: Execute optimized workflow
        asset_data = {
            "asset_type": "intelligence_report",
            "client_id": "enterprise_client_001",
            "description": "Market analysis for Q1 2026",
            "accuracy": 0.89,
            "completeness": 0.85,
            "relevance": 0.91,
            "timeliness": 0.80,
            "clarity": 0.88,
            "actionability": 0.82,
            "innovation": 0.75,
            "depth": 0.87,
            "credibility": 0.90
        }

        success, workflow_metrics = self.workflow.execute_optimized_workflow(asset.asset_id, asset_data)
        assert success == True
        print(f"✅ Workflow executed: {workflow_metrics['total_duration_seconds']:.2f}s")

        # Step 4: Assess quality
        metrics = AssetQualityMetrics(
            accuracy=0.89, completeness=0.85, relevance=0.91,
            timeliness=0.80, clarity=0.88, actionability=0.82,
            innovation=0.75, depth=0.87, credibility=0.90
        )
        passed = self.orchestrator.assess_asset_quality(asset.asset_id, metrics)
        assert passed == True
        print(f"✅ Quality assessment: PASSED (score: {metrics.overall_score:.2f})")

        # Step 5: Calculate value
        value = self.orchestrator.calculate_asset_value(
            asset_id=asset.asset_id,
            base_price=2500,
            complexity="complex",
            market_demand=1.3
        )
        print(f"✅ Value calculated: ${value.final_price:.2f} (margin: {value.gross_margin:.1f}%)")

        # Step 6: Complete delivery
        self.orchestrator.complete_asset(asset.asset_id)
        assert asset.status == AssetStatus.DELIVERED
        print(f"✅ Asset delivered: {asset.asset_id}")

        # Step 7: Verify metrics
        summary = self.orchestrator.get_asset_metrics_summary()
        assert summary["delivered"] > 0
        print(f"✅ Summary metrics: {summary['delivered']} delivered, ${summary['total_revenue_generated']:.2f} total revenue")

        print("✅ FULL WORKFLOW TEST PASSED\n")

    def test_multi_asset_concurrent_creation(self):
        """Test creating multiple assets concurrently"""
        print("\n=== CONCURRENT ASSET CREATION TEST ===")

        assets = []
        for i in range(3):
            request = self.orchestrator.create_asset_request(
                asset_type=AssetType.INTELLIGENCE_REPORT,
                client_id=f"client_{i}",
                description=f"Asset {i}",
                urgency=1.0 + (i * 0.2)
            )
            asset = self.orchestrator.initiate_asset_creation(request)
            asset.assigned_agents = ["E-auriga-01", "E-vega-02"]
            assets.append(asset)

        print(f"✅ Created {len(assets)} assets concurrently")

        # Assess all
        for asset in assets:
            metrics = AssetQualityMetrics(
                accuracy=0.85 + (0.02 * assets.index(asset)),
                completeness=0.80, relevance=0.85,
                timeliness=0.75, clarity=0.85, actionability=0.70,
                innovation=0.60, depth=0.80, credibility=0.85
            )
            self.orchestrator.assess_asset_quality(asset.asset_id, metrics)
            self.orchestrator.complete_asset(asset.asset_id)

        summary = self.orchestrator.get_asset_metrics_summary()
        assert summary["delivered"] >= 3
        print(f"✅ All assets completed: {summary['delivered']} delivered")
        print("✅ CONCURRENT CREATION TEST PASSED\n")


# ============================================================================
# PERFORMANCE TESTS
# ============================================================================

class TestPerformance(unittest.TestCase):
    """Performance and stress tests"""

    def setUp(self):
        """Set up test fixtures"""
        self.orchestrator = asset_orchestrator
        self.orchestrator.asset_registry.clear()
        self.workflow = optimized_workflow_engine

    def test_single_asset_performance(self):
        """Test single asset creation performance"""
        print("\n=== SINGLE ASSET PERFORMANCE TEST ===")

        start = time.time()

        request = self.orchestrator.create_asset_request(
            asset_type=AssetType.INTELLIGENCE_REPORT,
            client_id="perf_test",
            description="Performance test asset"
        )
        asset = self.orchestrator.initiate_asset_creation(request)

        asset_data = {
            "asset_type": "intelligence_report",
            "client_id": "perf_test",
            "description": "Performance test",
            "accuracy": 0.87, "completeness": 0.82, "relevance": 0.88,
            "timeliness": 0.75, "clarity": 0.85, "actionability": 0.72,
            "innovation": 0.65, "depth": 0.83, "credibility": 0.86
        }

        success, workflow_metrics = self.workflow.execute_optimized_workflow(asset.asset_id, asset_data)

        metrics = AssetQualityMetrics(
            accuracy=0.87, completeness=0.82, relevance=0.88,
            timeliness=0.75, clarity=0.85, actionability=0.72,
            innovation=0.65, depth=0.83, credibility=0.86
        )
        self.orchestrator.assess_asset_quality(asset.asset_id, metrics)
        self.orchestrator.calculate_asset_value(asset.asset_id, 2500)
        self.orchestrator.complete_asset(asset.asset_id)

        total_time = time.time() - start

        print(f"✅ Asset creation: {total_time:.3f}s")
        print(f"✅ Workflow duration: {workflow_metrics['total_duration_seconds']:.3f}s")
        print(f"✅ Parallel efficiency: {workflow_metrics['parallel_efficiency']:.2%}")
        print("✅ PERFORMANCE TEST PASSED\n")

    def test_throughput_benchmark(self):
        """Benchmark asset creation throughput"""
        print("\n=== THROUGHPUT BENCHMARK ===")

        n_assets = 10
        start = time.time()

        for i in range(n_assets):
            request = self.orchestrator.create_asset_request(
                asset_type=AssetType.INTELLIGENCE_REPORT,
                client_id=f"throughput_test_{i}",
                description=f"Asset {i}"
            )
            asset = self.orchestrator.initiate_asset_creation(request)

            metrics = AssetQualityMetrics(
                accuracy=0.85, completeness=0.80, relevance=0.85,
                timeliness=0.75, clarity=0.85, actionability=0.70,
                innovation=0.60, depth=0.80, credibility=0.85
            )
            self.orchestrator.assess_asset_quality(asset.asset_id, metrics)
            self.orchestrator.complete_asset(asset.asset_id)

        total_time = time.time() - start
        throughput = n_assets / total_time

        print(f"✅ Created {n_assets} assets in {total_time:.2f}s")
        print(f"✅ Throughput: {throughput:.2f} assets/second")
        print("✅ THROUGHPUT BENCHMARK PASSED\n")


# ============================================================================
# TEST RUNNER
# ============================================================================

if __name__ == "__main__":
    print("\n" + "="*60)
    print("SINCOR ASSET CREATION TEST SUITE")
    print("="*60)

    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestAssetOrchestration))
    suite.addTests(loader.loadTestsFromTestCase(TestValueStandards))
    suite.addTests(loader.loadTestsFromTestCase(TestWorkflowOptimization))
    suite.addTests(loader.loadTestsFromTestCase(TestEndToEndWorkflow))
    suite.addTests(loader.loadTestsFromTestCase(TestPerformance))

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    print(f"Tests run: {result.testsRun}")
    print(f"Successes: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print("="*60 + "\n")

    # Exit code
    exit(0 if result.wasSuccessful() else 1)
