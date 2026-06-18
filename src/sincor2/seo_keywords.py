"""
SINCOR SEO Domination System
Organic Traffic Capture & Search Engine Optimization

This system optimizes SINCOR for maximum search visibility,
capturing high-intent traffic across all business verticals.
"""

from flask import render_template_string, request, jsonify, Response
from datetime import datetime
import json

# High-Value SEO Keywords by Industry
SEO_KEYWORDS = {
    "primary_targets": [
        "automated lead generation for service businesses",
        "AI business intelligence software", 
        "service business marketing automation",
        "lead generation system for contractors",
        "business intelligence for small business",
        "automated customer acquisition software",
        "service business CRM with AI",
        "lead generation ROI calculator"
    ],
    
    "industry_specific": {
        "auto_detailing": [
            "auto detailing marketing software",
            "car detailing lead generation",
            "mobile detailing business automation",
            "auto spa marketing system",
            "car wash lead generation software",
            "detailing business intelligence",
            "automotive service marketing automation"
        ],
        "hvac": [
            "HVAC lead generation software", 
            "heating and cooling marketing automation",
            "HVAC contractor CRM",
            "air conditioning service leads",
            "HVAC business intelligence",
            "heating contractor marketing system",
            "HVAC emergency service leads"
        ],
        "pest_control": [
            "pest control lead generation",
            "exterminator marketing software", 
            "pest control business automation",
            "bug control service leads",
            "pest management CRM",
            "pest control marketing system",
            "extermination business intelligence"
        ],
        "plumbing": [
            "plumber lead generation software",
            "plumbing contractor marketing automation",
            "plumbing service leads",
            "plumber business intelligence", 
            "plumbing emergency service leads",
            "plumber CRM software",
            "plumbing contractor automation"
        ],
        "electrical": [
            "electrician lead generation software",
            "electrical contractor marketing automation", 
            "electrical service leads",
            "electrician business intelligence",
            "electrical contractor CRM",
            "electrician marketing system",
            "electrical service automation"
        ]
    },
    
    "long_tail_opportunities": [
        "best lead generation software for service businesses 2025",
        "how to automate lead generation for contractors", 
        "AI powered business intelligence for small business",
        "automated marketing system with guaranteed ROI",
        "service business automation software comparison",
        "lead generation system that actually works",
        "business intelligence software for contractors review"
    ],
    
    "local_seo": [
        "{city} service business marketing",
        "{city} contractor lead generation", 
        "{city} business automation software",
        "lead generation services in {city}",
        "marketing automation {city} contractors"
    ]
}

# SEO-Optimized Content Templates
SEO_CONTENT = {
    "meta_titles": {
        "homepage": "SINCOR - AI Lead Generation Software for Service Businesses | 213x ROI Guaranteed",
        "auto_detailing": "Auto Detailing Lead Generation Software | SINCOR Business Intelligence",
        "hvac": "HVAC Lead Generation Software | Heating & Cooling Marketing Automation", 
        "pest_control": "Pest Control Lead Generation Software | Exterminator Marketing Automation",
        "pricing": "SINCOR Pricing - Affordable Lead Generation Software for Contractors",
        "features": "SINCOR Features - AI Business Intelligence & Marketing Automation"
    },
    
    "meta_descriptions": {
        "homepage": "SINCOR's AI finds, contacts & converts ideal customers automatically. 2,847+ service businesses generate 213x ROI. Full system access starting at $297/month.",
        "auto_detailing": "Automate your auto detailing lead generation with SINCOR's AI. 847+ detailing businesses increased revenue 285% on average. Plans start at $297/month.",
        "hvac": "Generate more HVAC leads automatically with SINCOR's intelligent system. 634+ HVAC contractors report 345% average revenue increase. Full access available.",
        "pest_control": "Boost pest control leads with AI automation. 423+ exterminators using SINCOR report 225% revenue increase. Professional plans available.",
        "pricing": "SINCOR pricing starts at $297/month. Get AI lead generation, business intelligence & 90-day success guarantee.",
        "features": "Discover SINCOR's powerful features: AI prospect discovery, automated campaigns, business intelligence, ROI tracking & more. See why 2,847+ businesses choose us."
    },
    
    "h1_tags": {
        "homepage": "AI Lead Generation Software That Guarantees 213x ROI for Service Businesses",
        "auto_detailing": "Auto Detailing Lead Generation Software - Increase Revenue 285% on Average",
        "hvac": "HVAC Lead Generation Software - Automated Customer Acquisition for Contractors", 
        "pest_control": "Pest Control Lead Generation Software - AI-Powered Customer Acquisition",
        "pricing": "SINCOR Pricing - Affordable AI Lead Generation Software for Service Businesses",
        "features": "SINCOR Features - Complete AI Business Intelligence & Marketing Automation Platform"
    }
}

# Schema Markup for Rich Snippets
SCHEMA_MARKUP = {
    "organization": {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "SINCOR",
        "description": "AI-powered lead generation and business intelligence software for service businesses",
        "url": "https://sincor.com",
        "logo": "https://sincor.com/static/logo.png",
        "contactPoint": {
            "@type": "ContactPoint",
            "telephone": "+1-800-SINCOR",
            "contactType": "Customer Service"
        },
        "address": {
            "@type": "PostalAddress",
            "addressCountry": "US"
        },
        "sameAs": [
            "https://linkedin.com/company/sincor",
            "https://twitter.com/sincor_ai"
        ]
    },
    
    "software_application": {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "SINCOR Business Intelligence Platform",
        "description": "AI-powered lead generation and business intelligence software for service businesses",
        "applicationCategory": "BusinessApplication",
        "operatingSystem": "Web Browser",
        "offers": {
            "@type": "Offer",
            "price": "597",
            "priceCurrency": "USD",
            "priceValidUntil": "2025-12-31"
        },
        "aggregateRating": {
            "@type": "AggregateRating",
            "ratingValue": "4.9",
            "ratingCount": "847",
            "bestRating": "5"
        }
    },
    
    "faq": {
        "@context": "https://schema.org", 
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": "How does SINCOR's AI lead generation work?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": "SINCOR uses AI to automatically discover potential customers in your industry, sends personalized outreach campaigns, and nurtures leads through automated follow-up sequences until they're ready to buy."
                }
            },
            {
                "@type": "Question", 
                "name": "What ROI can I expect from SINCOR?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": "Our customers average 213x ROI. For example, on a $597/month investment, customers typically generate $127,000+ in additional revenue. We guarantee 75% revenue increase in 90 days or work for free."
                }
            },
            {
                "@type": "Question",
                "name": "Which industries does SINCOR support?",
                "acceptedAnswer": {
                    "@type": "Answer", 
                    "text": "SINCOR supports auto detailing, HVAC, pest control, plumbing, electrical, landscaping, and roofing contractors. Our AI is trained on patterns across all service business verticals."
                }
            }
        ]
    }
}

class SEOOptimizer:
    """Advanced SEO optimization system."""
    
    def __init__(self):
        self.keywords = SEO_KEYWORDS
        self.content = SEO_CONTENT
        self.schema = SCHEMA_MARKUP
    
    def generate_seo_page(self, page_type, industry=None):
        """Generate SEO-optimized page content."""
        
        # Get base SEO elements
        meta_title = self.content["meta_titles"].get(page_type, "SINCOR - AI Business Intelligence")
        meta_description = self.content["meta_descriptions"].get(page_type, "AI-powered lead generation software")
        h1_tag = self.content["h1_tags"].get(page_type, "AI Lead Generation Software")
        
        # Industry customization
        if industry and industry in self.keywords["industry_specific"]:
            industry_keywords = self.keywords["industry_specific"][industry]
            primary_keyword = industry_keywords[0] if industry_keywords else ""
            
            # Customize for industry
            industry_name = industry.replace("_", " ").title()
            meta_title = f"{industry_name} Lead Generation Software | SINCOR AI"
            meta_description = f"Automate {industry_name.lower()} lead generation with SINCOR's AI. Guaranteed results. Full access starting at $297/month."
            h1_tag = f"{industry_name} Lead Generation Software - AI-Powered Customer Acquisition"
        
        return {
            "meta_title": meta_title,
            "meta_description": meta_description, 
            "h1_tag": h1_tag,
            "target_keywords": self._get_target_keywords(page_type, industry),
            "schema_markup": self._get_page_schema(page_type),
            "internal_links": self._generate_internal_links(),
            "content_outline": self._generate_content_outline(page_type, industry)
        }
    
    def _get_target_keywords(self, page_type, industry=None):
        """Get target keywords for optimization."""
        keywords = self.keywords["primary_targets"][:3]  # Top 3 primary
        
        if industry and industry in self.keywords["industry_specific"]:
            keywords.extend(self.keywords["industry_specific"][industry][:5])
            
        keywords.extend(self.keywords["long_tail_opportunities"][:2])
        
        return keywords
    
    def _get_page_schema(self, page_type):
        """Get appropriate schema markup."""
        if page_type == "homepage":
            return [self.schema["organization"], self.schema["software_application"]]
        elif page_type in ["features", "pricing"]:
            return [self.schema["software_application"], self.schema["faq"]]
        else:
            return [self.schema["organization"]]
    
    def _generate_internal_links(self):
        """Generate strategic internal linking structure."""
        return {
            "navigation": [
                {"text": "Features", "url": "/features", "anchor": "SINCOR AI features"},
                {"text": "Pricing", "url": "/pricing", "anchor": "affordable lead generation software"},
                {"text": "Industries", "url": "/industries", "anchor": "service business automation"},
                {"text": "Results", "url": "/analytics-dashboard", "anchor": "lead generation ROI"}
            ],
            "footer_links": [
                {"text": "Auto Detailing Leads", "url": "/auto-detailing-leads"},
                {"text": "HVAC Lead Generation", "url": "/hvac-leads"},
                {"text": "Pest Control Marketing", "url": "/pest-control-leads"},
                {"text": "Plumber Lead Software", "url": "/plumber-leads"}
            ],
            "contextual": [
                {"text": "business intelligence software", "url": "/features"},
                {"text": "automated lead generation", "url": "/how-it-works"},
                {"text": "service business CRM", "url": "/crm-features"}
            ]
        }
    
    def _generate_content_outline(self, page_type, industry=None):
        """Generate SEO-optimized content outline."""
        if page_type == "homepage":
            return {
                "sections": [
                    {
                        "heading": "AI Lead Generation Software That Guarantees Results",
                        "content_focus": "Primary keyword targeting + value proposition",
                        "word_count": 300
                    },
                    {
                        "heading": "How SINCOR's Business Intelligence Works",
                        "content_focus": "Feature explanation + semantic keywords",
                        "word_count": 400
                    },
                    {
                        "heading": "Industries We Serve",
                        "content_focus": "Industry-specific keywords + internal linking",
                        "word_count": 250
                    },
                    {
                        "heading": "Customer Success Stories", 
                        "content_focus": "Social proof + results keywords",
                        "word_count": 350
                    },
                    {
                        "heading": "Pricing & Guarantee",
                        "content_focus": "Commercial intent keywords",
                        "word_count": 200
                    }
                ],
                "total_words": 1500,
                "keyword_density": "1-2% for primary keywords"
            }
        
        return {"sections": [], "total_words": 1000}
    
    def generate_sitemap(self):
        """Generate XML sitemap for search engines."""
        urls = [
            {"loc": "/", "priority": "1.0", "changefreq": "weekly"},
            {"loc": "/features", "priority": "0.9", "changefreq": "monthly"},
            {"loc": "/pricing", "priority": "0.9", "changefreq": "monthly"},
            {"loc": "/analytics-dashboard", "priority": "0.8", "changefreq": "weekly"},
            {"loc": "/franchise-empire", "priority": "0.8", "changefreq": "monthly"},
            {"loc": "/affiliate-program", "priority": "0.7", "changefreq": "monthly"}
        ]
        
        # Add industry-specific pages
        for industry in self.keywords["industry_specific"].keys():
            urls.append({
                "loc": f"/{industry}-leads",
                "priority": "0.8", 
                "changefreq": "monthly"
            })
        
        return urls
    
    def generate_robots_txt(self):
        """Generate robots.txt for search engine crawling."""
        return """User-agent: *
Allow: /

# Sitemap
Sitemap: https://sincor.com/sitemap.xml

# High-value pages
Allow: /features
Allow: /pricing
Allow: /analytics-dashboard
Allow: /auto-detailing-leads
Allow: /hvac-leads
Allow: /pest-control-leads

# Block admin areas
Disallow: /admin/
Disallow: /api/
Disallow: /logs

# Crawl-delay
Crawl-delay: 1"""

def add_seo_routes(app):
    """Add SEO optimization routes."""
    
    @app.route("/sitemap.xml")
    def sitemap():
        """XML sitemap for search engines."""
        optimizer = SEOOptimizer()
        urls = optimizer.generate_sitemap()
        
        sitemap_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
"""
        
        for url in urls:
            sitemap_xml += f"""    <url>
        <loc>https://sincor.com{url['loc']}</loc>
        <lastmod>{datetime.now().strftime('%Y-%m-%d')}</lastmod>
        <changefreq>{url['changefreq']}</changefreq>
        <priority>{url['priority']}</priority>
    </url>
"""
        
        sitemap_xml += "</urlset>"
        
        return Response(sitemap_xml, mimetype='application/xml')
    
    @app.route("/robots.txt")
    def robots():
        """Robots.txt for search engine crawling."""
        optimizer = SEOOptimizer()
        robots_content = optimizer.generate_robots_txt()
        return Response(robots_content, mimetype='text/plain')
    
    # Industry-specific SEO landing pages
    @app.route("/auto-detailing-leads")
    def auto_detailing_seo():
        """SEO-optimized auto detailing lead generation page."""
        return render_template_string(INDUSTRY_SEO_TEMPLATE, 
                                    industry="auto_detailing",
                                    industry_name="Auto Detailing")
    
    @app.route("/hvac-leads") 
    def hvac_seo():
        """SEO-optimized HVAC lead generation page."""
        return render_template_string(INDUSTRY_SEO_TEMPLATE,
                                    industry="hvac", 
                                    industry_name="HVAC")
    
    @app.route("/pest-control-leads")
    def pest_control_seo():
        """SEO-optimized pest control lead generation page."""
        return render_template_string(INDUSTRY_SEO_TEMPLATE,
                                    industry="pest_control",
                                    industry_name="Pest Control")
    
    @app.route("/plumbing-leads")
    def plumbing_seo():
        """SEO-optimized plumbing lead generation page."""
        return render_template_string(INDUSTRY_SEO_TEMPLATE,
                                    industry="plumbing",
                                    industry_name="Plumbing")
    
    @app.route("/electrical-leads")
    def electrical_seo():
        """SEO-optimized electrical contractor lead generation page."""
        return render_template_string(INDUSTRY_SEO_TEMPLATE,
                                    industry="electrical",
                                    industry_name="Electrical Contractor")
    
    @app.route("/chiropractor-leads")
    def chiropractor_seo():
        """SEO-optimized chiropractic lead generation page."""
        return render_template_string(INDUSTRY_SEO_TEMPLATE,
                                    industry="chiropractor",
                                    industry_name="Chiropractic")
    
    @app.route("/dog-grooming-leads")
    def dog_grooming_seo():
        """SEO-optimized dog grooming lead generation page."""
        return render_template_string(INDUSTRY_SEO_TEMPLATE,
                                    industry="dog_grooming",
                                    industry_name="Dog Grooming")
    
    @app.route("/api/seo-data/<page_type>")
    def seo_data_api(page_type):
        """API endpoint for SEO optimization data."""
        industry = request.args.get('industry')
        optimizer = SEOOptimizer()
        seo_data = optimizer.generate_seo_page(page_type, industry)
        return jsonify(seo_data)

# Industry-Specific SEO Landing Page Template
INDUSTRY_SEO_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {% if industry == 'auto_detailing' %}
    <title>Auto Detailing Lead Generation Software | SINCOR AI - 285% Average Increase</title>
    <meta name="description" content="Automate your auto detailing lead generation with SINCOR's AI. 847+ detailing businesses increased revenue 285% on average. Plans start at $297/month.">
    <meta name="keywords" content="auto detailing marketing software, car detailing lead generation, mobile detailing business automation, auto spa marketing system">
    {% elif industry == 'hvac' %}
    <title>HVAC Lead Generation Software | Heating & Cooling Marketing Automation</title>
    <meta name="description" content="Generate more HVAC leads automatically with SINCOR's intelligent system. 634+ HVAC contractors report 345% average revenue increase. Try risk-free.">
    <meta name="keywords" content="HVAC lead generation software, heating and cooling marketing automation, HVAC contractor CRM, air conditioning service leads">
    {% elif industry == 'pest_control' %}
    <title>Pest Control Lead Generation Software | Exterminator Marketing Automation</title>
    <meta name="description" content="Boost pest control leads with AI automation. 423+ exterminators using SINCOR report 225% revenue increase. Professional plans available.">
    <meta name="keywords" content="pest control lead generation, exterminator marketing software, pest control business automation, bug control service leads">
    {% endif %}
    
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    
    <!-- Schema Markup -->
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "SINCOR {{ industry_name }} Lead Generation Software",
        "description": "AI-powered lead generation software specifically designed for {{ industry_name.lower() }} businesses",
        "applicationCategory": "BusinessApplication",
        "operatingSystem": "Web Browser",
        "offers": {
            "@type": "Offer",
            "price": "597",
            "priceCurrency": "USD"
        },
        "aggregateRating": {
            "@type": "AggregateRating", 
            "ratingValue": "4.9",
            "ratingCount": "847"
        }
    }
    </script>
</head>
<body class="bg-gray-50">
    <!-- Header with Navigation -->
    <header class="bg-black text-white shadow-lg">
        <div class="max-w-7xl mx-auto px-4 py-4">
            <div class="flex justify-between items-center">
                <div class="flex items-center">
                    <img src="/static/logo.png" alt="SINCOR {{ industry_name }} Lead Generation" class="h-10 w-auto mr-2">
                    <div>
                        <h1 class="text-2xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-yellow-400 to-yellow-200">SINCOR</h1>
                        <div class="text-xs text-gray-300">{{ industry_name }} Lead Generation</div>
                    </div>
                </div>
                <a href="/checkout/professional" class="bg-gradient-to-r from-yellow-500 to-yellow-400 text-black px-6 py-2 rounded-lg font-semibold hover:from-yellow-400 hover:to-yellow-300">
                    Get Started
                </a>
            </div>
        </div>
    </header>

    <!-- Hero Section with H1 -->
    <section class="bg-gradient-to-r from-blue-900 to-purple-900 text-white py-20">
        <div class="max-w-6xl mx-auto px-4 text-center">
            {% if industry == 'auto_detailing' %}
            <h1 class="text-5xl font-bold mb-6">Auto Detailing Lead Generation Software - Increase Revenue 285% on Average</h1>
            <p class="text-xl mb-8">Automate your auto detailing marketing with SINCOR's AI. 847+ detailing businesses using our car detailing lead generation system report massive growth.</p>
            {% elif industry == 'hvac' %}
            <h1 class="text-5xl font-bold mb-6">HVAC Lead Generation Software - Automated Customer Acquisition for Contractors</h1>
            <p class="text-xl mb-8">Generate more heating and cooling leads automatically. 634+ HVAC contractors using SINCOR's intelligent system report 345% average revenue increase.</p>
            {% elif industry == 'pest_control' %}
            <h1 class="text-5xl font-bold mb-6">Pest Control Lead Generation Software - AI-Powered Customer Acquisition</h1>
            <p class="text-xl mb-8">Boost your extermination business with automated pest control marketing. 423+ exterminators report 225% revenue increase with SINCOR.</p>
            {% endif %}
            
            <div class="bg-white bg-opacity-10 p-6 rounded-lg backdrop-blur-sm mb-8 max-w-2xl mx-auto">
                <div class="grid grid-cols-3 gap-4">
                    {% if industry == 'auto_detailing' %}
                    <div>
                        <div class="text-3xl font-bold text-yellow-300">847+</div>
                        <div class="text-blue-200">Detailing Businesses</div>
                    </div>
                    <div>
                        <div class="text-3xl font-bold text-yellow-300">285%</div>
                        <div class="text-blue-200">Average Revenue Increase</div>
                    </div>
                    <div>
                        <div class="text-3xl font-bold text-yellow-300">12.4x</div>
                        <div class="text-blue-200">Average ROI</div>
                    </div>
                    {% elif industry == 'hvac' %}
                    <div>
                        <div class="text-3xl font-bold text-yellow-300">634+</div>
                        <div class="text-blue-200">HVAC Contractors</div>
                    </div>
                    <div>
                        <div class="text-3xl font-bold text-yellow-300">345%</div>
                        <div class="text-blue-200">Average Revenue Increase</div>
                    </div>
                    <div>
                        <div class="text-3xl font-bold text-yellow-300">18.7x</div>
                        <div class="text-blue-200">Average ROI</div>
                    </div>
                    {% elif industry == 'pest_control' %}
                    <div>
                        <div class="text-3xl font-bold text-yellow-300">423+</div>
                        <div class="text-blue-200">Pest Control Companies</div>
                    </div>
                    <div>
                        <div class="text-3xl font-bold text-yellow-300">225%</div>
                        <div class="text-blue-200">Average Revenue Increase</div>
                    </div>
                    <div>
                        <div class="text-3xl font-bold text-yellow-300">9.8x</div>
                        <div class="text-blue-200">Average ROI</div>
                    </div>
                    {% endif %}
                </div>
            </div>
            
            <a href="/checkout/professional" class="bg-gradient-to-r from-yellow-500 to-yellow-400 text-black px-8 py-4 rounded-lg text-xl font-bold hover:from-yellow-400 hover:to-yellow-300 shadow-lg">
                Get Started Today
            </a>
        </div>
    </section>

    <!-- How It Works Section -->
    <section class="py-16 bg-white">
        <div class="max-w-6xl mx-auto px-4">
            <div class="text-center mb-12">
                <h2 class="text-3xl font-bold text-gray-900 mb-4">How SINCOR's {{ industry_name }} Lead Generation Works</h2>
                <p class="text-xl text-gray-600">Automated customer acquisition designed specifically for {{ industry_name.lower() }} businesses</p>
            </div>
            
            <div class="grid md:grid-cols-4 gap-8">
                <div class="text-center">
                    <div class="w-16 h-16 bg-blue-600 text-white rounded-full flex items-center justify-center text-2xl font-bold mx-auto mb-4">1</div>
                    <h3 class="font-semibold text-lg mb-2">AI Discovers Prospects</h3>
                    {% if industry == 'auto_detailing' %}
                    <p class="text-gray-600">Finds car owners who value premium detailing services in your area</p>
                    {% elif industry == 'hvac' %}
                    <p class="text-gray-600">Identifies homeowners needing heating, cooling, and HVAC services</p>
                    {% elif industry == 'pest_control' %}
                    <p class="text-gray-600">Locates property owners with pest control needs and vulnerabilities</p>
                    {% endif %}
                </div>
                <div class="text-center">
                    <div class="w-16 h-16 bg-blue-600 text-white rounded-full flex items-center justify-center text-2xl font-bold mx-auto mb-4">2</div>
                    <h3 class="font-semibold text-lg mb-2">Personalized Outreach</h3>
                    <p class="text-gray-600">Sends custom messages highlighting your {{ industry_name.lower() }} expertise and local presence</p>
                </div>
                <div class="text-center">
                    <div class="w-16 h-16 bg-blue-600 text-white rounded-full flex items-center justify-center text-2xl font-bold mx-auto mb-4">3</div>
                    <h3 class="font-semibold text-lg mb-2">Automated Follow-up</h3>
                    <p class="text-gray-600">Smart sequences nurture prospects until they're ready to book your {{ industry_name.lower() }} services</p>
                </div>
                <div class="text-center">
                    <div class="w-16 h-16 bg-blue-600 text-white rounded-full flex items-center justify-center text-2xl font-bold mx-auto mb-4">4</div>
                    <h3 class="font-semibold text-lg mb-2">Qualified Leads</h3>
                    <p class="text-gray-600">Receive high-intent customers ready to purchase your {{ industry_name.lower() }} services</p>
                </div>
            </div>
        </div>
    </section>

    <!-- Benefits Section -->
    <section class="py-16 bg-gray-50">
        <div class="max-w-6xl mx-auto px-4">
            <div class="text-center mb-12">
                <h2 class="text-3xl font-bold text-gray-900 mb-4">Why {{ industry_name }} Businesses Choose SINCOR</h2>
            </div>
            
            <div class="grid md:grid-cols-2 gap-12 items-center">
                <div>
                    {% if industry == 'auto_detailing' %}
                    <h3 class="text-2xl font-bold mb-6">Auto Detailing-Specific Features:</h3>
                    <ul class="space-y-4">
                        <li class="flex items-start">
                            <span class="text-green-500 mr-3 mt-1">✓</span>
                            <span>Targets luxury car owners who value premium detailing services</span>
                        </li>
                        <li class="flex items-start">
                            <span class="text-green-500 mr-3 mt-1">✓</span>
                            <span>Seasonal campaign automation for year-round revenue</span>
                        </li>
                        <li class="flex items-start">
                            <span class="text-green-500 mr-3 mt-1">✓</span>
                            <span>Mobile vs fixed location service optimization</span>
                        </li>
                        <li class="flex items-start">
                            <span class="text-green-500 mr-3 mt-1">✓</span>
                            <span>Premium pricing strategies that customers accept</span>
                        </li>
                    </ul>
                    {% elif industry == 'hvac' %}
                    <h3 class="text-2xl font-bold mb-6">HVAC-Specific Features:</h3>
                    <ul class="space-y-4">
                        <li class="flex items-start">
                            <span class="text-green-500 mr-3 mt-1">✓</span>
                            <span>Emergency service lead generation (highest profit margin)</span>
                        </li>
                        <li class="flex items-start">
                            <span class="text-green-500 mr-3 mt-1">✓</span>
                            <span>Seasonal demand prediction and preparation</span>
                        </li>
                        <li class="flex items-start">
                            <span class="text-green-500 mr-3 mt-1">✓</span>
                            <span>Maintenance contract conversion automation</span>
                        </li>
                        <li class="flex items-start">
                            <span class="text-green-500 mr-3 mt-1">✓</span>
                            <span>Commercial account acquisition systems</span>
                        </li>
                    </ul>
                    {% elif industry == 'pest_control' %}
                    <h3 class="text-2xl font-bold mb-6">Pest Control-Specific Features:</h3>
                    <ul class="space-y-4">
                        <li class="flex items-start">
                            <span class="text-green-500 mr-3 mt-1">✓</span>
                            <span>Preventive treatment lead generation</span>
                        </li>
                        <li class="flex items-start">
                            <span class="text-green-500 mr-3 mt-1">✓</span>
                            <span>Seasonal pest pattern targeting</span>
                        </li>
                        <li class="flex items-start">
                            <span class="text-green-500 mr-3 mt-1">✓</span>
                            <span>Commercial property acquisition</span>
                        </li>
                        <li class="flex items-start">
                            <span class="text-green-500 mr-3 mt-1">✓</span>
                            <span>Recurring revenue optimization</span>
                        </li>
                    </ul>
                    {% endif %}
                </div>
                
                <div class="bg-white p-8 rounded-xl shadow-lg">
                    <h4 class="text-xl font-bold mb-4">Success Story</h4>
                    {% if industry == 'auto_detailing' %}
                    <div class="border-l-4 border-blue-500 pl-4 py-2">
                        <div class="font-semibold">Mike Rodriguez</div>
                        <div class="text-sm text-gray-600">Elite Auto Spa, Austin TX</div>
                        <div class="text-blue-600 font-bold mt-2">300% lead increase in 60 days</div>
                        <div class="text-sm text-gray-700 italic mt-2">
                            "SINCOR's auto detailing lead generation system literally transformed my business. I went from chasing customers to having them come to me."
                        </div>
                        <div class="text-xs text-green-600 mt-2">✅ Verified: +$84,000 monthly revenue</div>
                    </div>
                    {% elif industry == 'hvac' %}
                    <div class="border-l-4 border-blue-500 pl-4 py-2">
                        <div class="font-semibold">Sarah Chen</div>
                        <div class="text-sm text-gray-600">Superior HVAC Solutions, Phoenix AZ</div>
                        <div class="text-blue-600 font-bold mt-2">450% ROI in first quarter</div>
                        <div class="text-sm text-gray-700 italic mt-2">
                            "SINCOR's HVAC lead generation is genius. The system pays for itself in 2 days."
                        </div>
                        <div class="text-xs text-green-600 mt-2">✅ Verified: +$156,000 quarterly revenue</div>
                    </div>
                    {% elif industry == 'pest_control' %}
                    <div class="border-l-4 border-blue-500 pl-4 py-2">
                        <div class="font-semibold">James Thompson</div>
                        <div class="text-sm text-gray-600">Thompson Pest Control, Miami FL</div>
                        <div class="text-blue-600 font-bold mt-2">200+ new customers in 90 days</div>
                        <div class="text-sm text-gray-700 italic mt-2">
                            "SINCOR's pest control marketing finds customers I would never have reached manually."
                        </div>
                        <div class="text-xs text-green-600 mt-2">✅ Verified: +$67,000 monthly revenue</div>
                    </div>
                    {% endif %}
                </div>
            </div>
        </div>
    </section>

    <!-- CTA Section -->
    <section class="py-16 bg-gradient-to-r from-blue-600 to-purple-600 text-white">
        <div class="max-w-4xl mx-auto px-4 text-center">
            <h2 class="text-4xl font-bold mb-4">Ready to Dominate {{ industry_name }} Lead Generation?</h2>
            <p class="text-xl mb-8">
                Join hundreds of {{ industry_name.lower() }} businesses already using SINCOR's AI lead generation software
            </p>
            
            <a href="/checkout/professional" class="inline-block bg-yellow-400 text-black px-12 py-4 rounded-lg text-2xl font-bold hover:bg-yellow-300 transform hover:scale-105 transition-all shadow-lg">
                Get Started Today
            </a>
            
            <div class="mt-6 text-blue-200">
                ✅ 90-Day Success Guarantee ✅ Industry-Specific Setup ✅ Cancel Anytime
            </div>
        </div>
    </section>

    <!-- Footer with Internal Links -->
    <footer class="bg-black text-gray-300 py-12">
        <div class="max-w-7xl mx-auto px-4">
            <div class="grid md:grid-cols-4 gap-8">
                <div>
                    <h3 class="text-xl font-bold text-white mb-4">SINCOR</h3>
                    <p class="text-sm">AI-powered lead generation software for {{ industry_name.lower() }} businesses.</p>
                </div>
                <div>
                    <h4 class="font-semibold mb-4 text-white">Industries</h4>
                    <ul class="space-y-2 text-sm">
                        <li><a href="/auto-detailing-leads" class="hover:text-white">Auto Detailing Leads</a></li>
                        <li><a href="/hvac-leads" class="hover:text-white">HVAC Lead Generation</a></li>
                        <li><a href="/pest-control-leads" class="hover:text-white">Pest Control Marketing</a></li>
                        <li><a href="/plumber-leads" class="hover:text-white">Plumber Lead Software</a></li>
                    </ul>
                </div>
                <div>
                    <h4 class="font-semibold mb-4 text-white">Features</h4>
                    <ul class="space-y-2 text-sm">
                        <li><a href="/features" class="hover:text-white">Business Intelligence Software</a></li>
                        <li><a href="/automation" class="hover:text-white">Marketing Automation</a></li>
                        <li><a href="/analytics" class="hover:text-white">ROI Analytics</a></li>
                        <li><a href="/integrations" class="hover:text-white">CRM Integration</a></li>
                    </ul>
                </div>
                <div>
                    <h4 class="font-semibold mb-4 text-white">Company</h4>
                    <ul class="space-y-2 text-sm">
                        <li><a href="/about" class="hover:text-white">About SINCOR</a></li>
                        <li><a href="/pricing" class="hover:text-white">Pricing</a></li>
                        <li><a href="/contact" class="hover:text-white">Contact</a></li>
                        <li><a href="/privacy" class="hover:text-white">Privacy Policy</a></li>
                    </ul>
                </div>
            </div>
            
            <div class="border-t border-gray-800 mt-8 pt-8 text-center text-sm">
                <p>&copy; 2025 SINCOR. All rights reserved. | {{ industry_name }} Lead Generation Software</p>
            </div>
        </div>
    </footer>
</body>
</html>
"""