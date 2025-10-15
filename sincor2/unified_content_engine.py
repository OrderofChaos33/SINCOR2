"""
SINCOR Unified Content Engine
Orchestrates all content generation capabilities into one cohesive system
Integrates: Quality Engine + Personalization Engine + Long-form Generation
"""

import asyncio
import json
from typing import Dict, List, Any, Optional
from datetime import datetime
from enum import Enum
from dataclasses import dataclass

from content_quality_engine import ContentQualityEngine, generate_quality_content
from content_personalization_engine import ContentPersonalizationEngine, personalize_for_audience


class ContentPackage(Enum):
    """Revenue-generating content packages"""
    MICRO = "micro"  # 1-5 pieces
    STANDARD = "standard"  # 10-20 pieces
    PROFESSIONAL = "professional"  # 30-50 pieces
    ENTERPRISE = "enterprise"  # 100+ pieces + custom
    LONGFORM = "longform"  # Books, whitepapers, comprehensive guides


class DeliverySpeed(Enum):
    """Content delivery speed tiers"""
    STANDARD = "standard"  # 5-7 days
    PRIORITY = "priority"  # 2-3 days
    RUSH = "rush"  # 24 hours
    EMERGENCY = "emergency"  # Same day


@dataclass
class ContentRequest:
    """Unified content generation request"""
    package_type: ContentPackage
    content_types: List[str]  # blog_post, product_description, landing_page, etc.
    industry: str
    target_audience: str
    brand_context: Dict[str, Any]
    keywords: List[str]
    tone: str = "professional"
    delivery_speed: DeliverySpeed = DeliverySpeed.STANDARD
    custom_requirements: Optional[Dict[str, Any]] = None


@dataclass
class ContentDeliverable:
    """Generated content package"""
    request_id: str
    package_type: ContentPackage
    generated_content: List[Dict[str, Any]]
    quality_scores: Dict[str, float]
    personalization_metadata: Dict[str, Any]
    total_word_count: int
    generated_at: str
    delivery_ready: bool
    export_formats: List[str]


class UnifiedContentEngine:
    """
    Master content orchestrator combining all content generation capabilities

    Features:
    - Quality-scored content generation
    - Audience personalization
    - Multi-format long-form content
    - Revenue-optimized packaging
    - Instant delivery pipelines
    """

    def __init__(self):
        self.quality_engine = ContentQualityEngine()
        self.personalization_engine = ContentPersonalizationEngine()

        # Package configurations
        self.package_configs = {
            ContentPackage.MICRO: {
                'pieces': (1, 5),
                'avg_words_per_piece': 500,
                'base_price': 500,
                'turnaround_days': 3
            },
            ContentPackage.STANDARD: {
                'pieces': (10, 20),
                'avg_words_per_piece': 800,
                'base_price': 2500,
                'turnaround_days': 7
            },
            ContentPackage.PROFESSIONAL: {
                'pieces': (30, 50),
                'avg_words_per_piece': 1000,
                'base_price': 6000,
                'turnaround_days': 14
            },
            ContentPackage.ENTERPRISE: {
                'pieces': (100, 200),
                'avg_words_per_piece': 1200,
                'base_price': 15000,
                'turnaround_days': 21
            },
            ContentPackage.LONGFORM: {
                'pieces': (1, 5),
                'avg_words_per_piece': 50000,  # Books/comprehensive guides
                'base_price': 25000,
                'turnaround_days': 30
            }
        }

        # Delivery speed multipliers
        self.speed_multipliers = {
            DeliverySpeed.STANDARD: 1.0,
            DeliverySpeed.PRIORITY: 1.8,
            DeliverySpeed.RUSH: 3.0,
            DeliverySpeed.EMERGENCY: 5.0
        }

        # Content type templates
        self.content_templates = {
            'blog_post': {'optimal_length': 1500, 'complexity': 'moderate'},
            'product_description': {'optimal_length': 250, 'complexity': 'simple'},
            'landing_page': {'optimal_length': 1000, 'complexity': 'moderate'},
            'email_campaign': {'optimal_length': 200, 'complexity': 'simple'},
            'social_media_post': {'optimal_length': 150, 'complexity': 'simple'},
            'white_paper': {'optimal_length': 5000, 'complexity': 'complex'},
            'case_study': {'optimal_length': 1200, 'complexity': 'moderate'},
            'sales_copy': {'optimal_length': 400, 'complexity': 'moderate'},
            'press_release': {'optimal_length': 600, 'complexity': 'moderate'},
            'ad_copy': {'optimal_length': 100, 'complexity': 'simple'},
            'video_script': {'optimal_length': 800, 'complexity': 'moderate'},
            'infographic_copy': {'optimal_length': 300, 'complexity': 'simple'},
            'ebook': {'optimal_length': 15000, 'complexity': 'complex'},
            'technical_documentation': {'optimal_length': 3000, 'complexity': 'complex'}
        }

        self.generation_history: List[Dict] = []

    async def generate_content_package(self, request: ContentRequest) -> ContentDeliverable:
        """
        Generate complete content package with quality assurance and personalization
        """
        request_id = f"CNT-{int(datetime.now().timestamp())}"

        generated_pieces = []
        total_words = 0
        quality_scores_aggregate = {}

        # Determine how many pieces to generate
        package_config = self.package_configs[request.package_type]
        num_pieces = self._calculate_piece_count(request, package_config)

        # Generate each content piece
        for i, content_type in enumerate(request.content_types[:num_pieces]):
            # Step 1: Generate base content with quality engine
            base_content = self.quality_engine.generate_content(
                content_type=content_type,
                specifications={
                    'topic': request.brand_context.get('topic', 'business solution'),
                    'audience': request.target_audience,
                    'tone': request.tone,
                    'keywords': request.keywords,
                    'word_count': self.content_templates.get(content_type, {}).get('optimal_length', 1000)
                }
            )

            # Step 2: Personalize for target audience
            personalized = self.personalization_engine.personalize_content(
                base_content=base_content['content'],
                target_audience=request.target_audience,
                industry=request.industry,
                goal=request.custom_requirements.get('goal', 'persuade') if request.custom_requirements else 'persuade'
            )

            # Step 3: Compile final deliverable
            piece = {
                'piece_id': f"{request_id}-{i+1}",
                'content_type': content_type,
                'base_content': base_content,
                'personalized_content': personalized,
                'quality_scores': base_content['quality_scores'],
                'seo_analysis': base_content['seo_analysis'],
                'readability': base_content['readability'],
                'word_count': base_content['word_count']
            }

            generated_pieces.append(piece)
            total_words += base_content['word_count']

            # Aggregate quality scores
            for metric, score in base_content['quality_scores'].items():
                if metric not in quality_scores_aggregate:
                    quality_scores_aggregate[metric] = []
                quality_scores_aggregate[metric].append(score)

        # Calculate average quality scores
        avg_quality_scores = {
            metric: sum(scores) / len(scores)
            for metric, scores in quality_scores_aggregate.items()
        }

        # Create deliverable
        deliverable = ContentDeliverable(
            request_id=request_id,
            package_type=request.package_type,
            generated_content=generated_pieces,
            quality_scores=avg_quality_scores,
            personalization_metadata={
                'target_audience': request.target_audience,
                'industry': request.industry,
                'tone': request.tone
            },
            total_word_count=total_words,
            generated_at=datetime.now().isoformat(),
            delivery_ready=True,
            export_formats=['html', 'pdf', 'docx', 'markdown']
        )

        # Log generation
        self.generation_history.append({
            'timestamp': datetime.now().isoformat(),
            'request_id': request_id,
            'package_type': request.package_type.value,
            'pieces_generated': len(generated_pieces),
            'total_words': total_words,
            'avg_quality_score': avg_quality_scores.get('overall', 0)
        })

        return deliverable

    def _calculate_piece_count(self, request: ContentRequest, config: Dict) -> int:
        """Calculate number of pieces to generate based on package and request"""
        min_pieces, max_pieces = config['pieces']

        # If specific count requested
        if request.custom_requirements and 'piece_count' in request.custom_requirements:
            return min(max_pieces, max(min_pieces, request.custom_requirements['piece_count']))

        # Default to minimum for package
        return min_pieces

    def calculate_package_pricing(self, request: ContentRequest) -> Dict[str, Any]:
        """Calculate dynamic pricing for content package"""
        config = self.package_configs[request.package_type]
        base_price = config['base_price']

        # Apply delivery speed multiplier
        speed_multiplier = self.speed_multipliers[request.delivery_speed]

        # Industry complexity multiplier
        industry_multipliers = {
            'fintech': 1.5,
            'healthcare': 1.4,
            'legal': 1.6,
            'saas': 1.2,
            'ecommerce': 1.0,
            'manufacturing': 1.1
        }
        industry_multiplier = industry_multipliers.get(request.industry, 1.0)

        # Audience complexity multiplier
        audience_multipliers = {
            'c_suite': 1.3,
            'technical_lead': 1.4,
            'director': 1.2,
            'manager': 1.0,
            'small_business_owner': 0.9
        }
        audience_multiplier = audience_multipliers.get(request.target_audience, 1.0)

        # Calculate final price
        final_price = base_price * speed_multiplier * industry_multiplier * audience_multiplier

        # Custom requirements adder
        if request.custom_requirements:
            if request.custom_requirements.get('seo_optimization'):
                final_price *= 1.2
            if request.custom_requirements.get('multilingual'):
                final_price *= 1.5
            if request.custom_requirements.get('rush_revisions'):
                final_price *= 1.3

        return {
            'base_price': base_price,
            'speed_multiplier': speed_multiplier,
            'industry_multiplier': industry_multiplier,
            'audience_multiplier': audience_multiplier,
            'final_price': round(final_price, 2),
            'estimated_delivery_days': config['turnaround_days'] / speed_multiplier,
            'pricing_breakdown': {
                'base': f"${base_price:,.2f}",
                'speed_adjustment': f"${base_price * (speed_multiplier - 1):,.2f}",
                'industry_adjustment': f"${base_price * (industry_multiplier - 1):,.2f}",
                'audience_adjustment': f"${base_price * (audience_multiplier - 1):,.2f}"
            }
        }

    async def generate_sales_materials(self, opportunity_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Auto-generate sales materials for a revenue opportunity
        Used by monetization engine to create instant deliverables
        """
        materials = {}

        # Generate pitch deck content
        pitch_request = ContentRequest(
            package_type=ContentPackage.MICRO,
            content_types=['sales_copy', 'case_study', 'product_description'],
            industry=opportunity_context.get('industry', 'saas'),
            target_audience=opportunity_context.get('audience', 'director'),
            brand_context={'topic': opportunity_context.get('topic', 'AI automation')},
            keywords=opportunity_context.get('keywords', ['efficiency', 'automation', 'ROI']),
            tone='persuasive',
            delivery_speed=DeliverySpeed.RUSH
        )

        materials['pitch_deck'] = await self.generate_content_package(pitch_request)

        # Generate follow-up email
        email_request = ContentRequest(
            package_type=ContentPackage.MICRO,
            content_types=['email_campaign'],
            industry=opportunity_context.get('industry', 'saas'),
            target_audience=opportunity_context.get('audience', 'director'),
            brand_context={'topic': opportunity_context.get('topic', 'AI automation')},
            keywords=opportunity_context.get('keywords', ['next steps', 'demo', 'ROI']),
            tone='professional',
            delivery_speed=DeliverySpeed.EMERGENCY
        )

        materials['follow_up_email'] = await self.generate_content_package(email_request)

        return materials

    def export_deliverable(self, deliverable: ContentDeliverable, format: str = 'markdown') -> str:
        """Export content deliverable in specified format"""
        if format == 'markdown':
            return self._export_markdown(deliverable)
        elif format == 'html':
            return self._export_html(deliverable)
        elif format == 'json':
            return json.dumps({
                'request_id': deliverable.request_id,
                'package_type': deliverable.package_type.value,
                'content': [piece for piece in deliverable.generated_content],
                'metadata': {
                    'quality_scores': deliverable.quality_scores,
                    'word_count': deliverable.total_word_count,
                    'generated_at': deliverable.generated_at
                }
            }, indent=2)
        else:
            return f"Format {format} not yet implemented"

    def _export_markdown(self, deliverable: ContentDeliverable) -> str:
        """Export as markdown format"""
        md_content = f"# Content Package: {deliverable.request_id}\n\n"
        md_content += f"**Package Type:** {deliverable.package_type.value}\n"
        md_content += f"**Generated:** {deliverable.generated_at}\n"
        md_content += f"**Total Words:** {deliverable.total_word_count:,}\n"
        md_content += f"**Overall Quality:** {deliverable.quality_scores.get('overall', 0):.1f}/100\n\n"
        md_content += "---\n\n"

        for i, piece in enumerate(deliverable.generated_content, 1):
            md_content += f"## Piece {i}: {piece['content_type']}\n\n"
            md_content += f"**Words:** {piece['word_count']}\n"
            md_content += f"**Quality Score:** {piece['quality_scores']['overall']:.1f}/100\n\n"

            # Add content sections
            for section_name, section_content in piece['base_content']['content'].items():
                md_content += f"### {section_name.replace('_', ' ').title()}\n\n"
                for paragraph in section_content:
                    md_content += f"{paragraph}\n\n"

            md_content += "---\n\n"

        return md_content

    def _export_html(self, deliverable: ContentDeliverable) -> str:
        """Export as HTML format"""
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Content Package {deliverable.request_id}</title>
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
                h1 {{ color: #333; }}
                .metadata {{ background: #f5f5f5; padding: 15px; margin: 20px 0; }}
                .piece {{ border: 1px solid #ddd; padding: 20px; margin: 20px 0; }}
                .quality-score {{ color: #4CAF50; font-weight: bold; }}
            </style>
        </head>
        <body>
            <h1>Content Package: {deliverable.request_id}</h1>
            <div class="metadata">
                <p><strong>Package Type:</strong> {deliverable.package_type.value}</p>
                <p><strong>Generated:</strong> {deliverable.generated_at}</p>
                <p><strong>Total Words:</strong> {deliverable.total_word_count:,}</p>
                <p class="quality-score">Overall Quality: {deliverable.quality_scores.get('overall', 0):.1f}/100</p>
            </div>
        """

        for i, piece in enumerate(deliverable.generated_content, 1):
            html += f"""
            <div class="piece">
                <h2>Piece {i}: {piece['content_type']}</h2>
                <p><strong>Words:</strong> {piece['word_count']}</p>
                <p class="quality-score">Quality: {piece['quality_scores']['overall']:.1f}/100</p>
            """

            for section_name, section_content in piece['base_content']['content'].items():
                html += f"<h3>{section_name.replace('_', ' ').title()}</h3>"
                for paragraph in section_content:
                    html += f"<p>{paragraph}</p>"

            html += "</div>"

        html += "</body></html>"
        return html


# Global unified engine instance
unified_engine = UnifiedContentEngine()


async def demo_unified_content_engine():
    """Demonstrate unified content engine capabilities"""
    print("=" * 70)
    print("SINCOR UNIFIED CONTENT ENGINE DEMO")
    print("=" * 70)

    engine = UnifiedContentEngine()

    # Demo 1: Standard content package
    print("\n[DEMO 1] Standard Content Package for SaaS Company")
    request = ContentRequest(
        package_type=ContentPackage.STANDARD,
        content_types=['blog_post', 'landing_page', 'case_study', 'product_description'],
        industry='saas',
        target_audience='director',
        brand_context={
            'topic': 'AI-powered business automation',
            'company': 'SINCOR',
            'value_prop': 'Intelligent swarm-based automation'
        },
        keywords=['AI', 'automation', 'efficiency', 'ROI', 'swarm intelligence'],
        tone='professional',
        delivery_speed=DeliverySpeed.PRIORITY
    )

    # Calculate pricing
    pricing = engine.calculate_package_pricing(request)
    print(f"  Package: {request.package_type.value}")
    print(f"  Delivery: {request.delivery_speed.value}")
    print(f"  Price: ${pricing['final_price']:,.2f}")
    print(f"  Estimated Delivery: {pricing['estimated_delivery_days']:.1f} days")

    # Generate package
    deliverable = await engine.generate_content_package(request)
    print(f"  ✅ Generated {len(deliverable.generated_content)} pieces")
    print(f"  📝 Total words: {deliverable.total_word_count:,}")
    print(f"  ⭐ Avg quality: {deliverable.quality_scores['overall']:.1f}/100")

    # Demo 2: Enterprise package
    print("\n[DEMO 2] Enterprise Content Package for Fintech")
    enterprise_request = ContentRequest(
        package_type=ContentPackage.PROFESSIONAL,
        content_types=['white_paper', 'case_study', 'blog_post'] * 10,
        industry='fintech',
        target_audience='c_suite',
        brand_context={'topic': 'Regulatory compliance automation'},
        keywords=['compliance', 'regulation', 'automation', 'risk'],
        tone='authoritative',
        delivery_speed=DeliverySpeed.STANDARD,
        custom_requirements={'seo_optimization': True}
    )

    pricing2 = engine.calculate_package_pricing(enterprise_request)
    print(f"  Package: {enterprise_request.package_type.value}")
    print(f"  Price: ${pricing2['final_price']:,.2f}")
    print(f"  Industry multiplier: {pricing2['industry_multiplier']}x")
    print(f"  Audience multiplier: {pricing2['audience_multiplier']}x")

    print("\n[SUCCESS] Unified content engine operational!")
    print(f"Generation history: {len(engine.generation_history)} packages created")

    return deliverable


if __name__ == "__main__":
    asyncio.run(demo_unified_content_engine())
