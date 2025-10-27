#!/usr/bin/env python3
"""
SINCOR Brand-Aligned Design
Matches the corporate logo: Navy Blue (#1a2852) + Gold (#d4af37) + Orange (#d87729)
Premium, professional, global enterprise aesthetic
"""

with open('templates/buy.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Find and replace the <style> section
old_style_start = '    <style>'
old_style_end = '    </style>'

style_start_idx = content.find(old_style_start)
style_end_idx = content.find(old_style_end) + len(old_style_end)

# SINCOR Brand Colors (from logo)
sincor_brand_css = '''    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=Playfair+Display:wght@700;800;900&display=swap');

        :root {
            /* SINCOR Brand Colors from Logo */
            --sincor-navy: #1a2852;
            --sincor-navy-light: #2a3862;
            --sincor-navy-dark: #0f1a3a;
            --sincor-gold: #d4af37;
            --sincor-gold-light: #e4bf47;
            --sincor-gold-dark: #b89a2f;
            --sincor-orange: #d87729;
            --sincor-orange-light: #e88739;
            --sincor-orange-dark: #c8671f;

            /* Neutral Palette */
            --white: #FFFFFF;
            --cream: #FAF8F3;
            --gray-50: #F9FAFB;
            --gray-100: #F3F4F6;
            --gray-200: #E5E7EB;
            --gray-600: #4B5563;
            --gray-900: #111827;

            /* Shadows */
            --shadow-sm: 0 2px 8px rgba(26, 40, 82, 0.08);
            --shadow-md: 0 4px 16px rgba(26, 40, 82, 0.12);
            --shadow-lg: 0 8px 32px rgba(26, 40, 82, 0.16);
            --shadow-xl: 0 16px 48px rgba(26, 40, 82, 0.20);
            --shadow-gold: 0 8px 24px rgba(212, 175, 55, 0.25);
        }

        * {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            background: linear-gradient(180deg, var(--cream) 0%, var(--white) 100%);
            color: var(--gray-900);
            line-height: 1.6;
            overflow-x: hidden;
        }

        /* Premium background accents - subtle navy/gold */
        body::before {
            content: '';
            position: fixed;
            top: -30%;
            right: -20%;
            width: 70%;
            height: 70%;
            background: radial-gradient(circle, rgba(26, 40, 82, 0.04) 0%, transparent 70%);
            pointer-events: none;
            z-index: 0;
        }

        body::after {
            content: '';
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            width: 40%;
            max-width: 500px;
            height: 40%;
            background: url('/static/images/sincor-logo.jpg') center/contain no-repeat;
            opacity: 0.03;
            pointer-events: none;
            z-index: 0;
        }

        /* Hero section logo watermark */
        .hero-section::after {
            content: '';
            position: absolute;
            top: 50%;
            right: 5%;
            transform: translateY(-50%);
            width: 300px;
            height: 300px;
            background: url('/static/images/sincor-logo.jpg') center/contain no-repeat;
            opacity: 0.06;
            pointer-events: none;
            z-index: 0;
        }

        /* Premium header - navy with gold accent */
        .premium-header {
            background: linear-gradient(135deg, var(--sincor-navy) 0%, var(--sincor-navy-light) 100%);
            border-bottom: 3px solid var(--sincor-gold);
            box-shadow: var(--shadow-md);
            position: sticky;
            top: 0;
            z-index: 1000;
        }

        .premium-header::after {
            content: '';
            position: absolute;
            bottom: -3px;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, var(--sincor-gold), var(--sincor-orange), var(--sincor-gold));
            opacity: 0.8;
        }

        /* Hero section - corporate premium */
        .hero-section {
            background: linear-gradient(135deg, var(--white) 0%, var(--cream) 100%);
            position: relative;
            padding: 80px 0;
            overflow: hidden;
        }

        .hero-section::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 400px;
            background:
                radial-gradient(circle at 30% 20%, rgba(26, 40, 82, 0.06) 0%, transparent 50%),
                radial-gradient(circle at 70% 60%, rgba(212, 175, 55, 0.04) 0%, transparent 50%);
            z-index: 0;
        }

        /* Title - matching logo serif style */
        .swarm-title {
            font-family: 'Playfair Display', serif;
            background: linear-gradient(135deg, var(--sincor-navy) 0%, var(--sincor-navy-light) 50%, var(--sincor-gold) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            font-weight: 900;
            font-size: 4rem;
            letter-spacing: -0.02em;
            line-height: 1.1;
            position: relative;
            z-index: 1;
        }

        /* Product cards - premium corporate */
        .product-card {
            background: var(--white);
            border: 2px solid var(--gray-200);
            border-radius: 16px;
            padding: 40px;
            transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
            box-shadow: var(--shadow-md);
            position: relative;
            overflow: hidden;
            z-index: 1;
        }

        .product-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 4px;
            background: linear-gradient(90deg, var(--sincor-navy), var(--sincor-gold), var(--sincor-orange));
            opacity: 0;
            transition: opacity 0.4s ease;
        }

        .product-card:hover {
            transform: translateY(-8px);
            border-color: var(--sincor-gold);
            box-shadow: var(--shadow-xl), var(--shadow-gold);
        }

        .product-card:hover::before {
            opacity: 1;
        }

        /* Badge styles - navy and gold corporate */
        .featured-badge {
            background: linear-gradient(135deg, var(--sincor-gold) 0%, var(--sincor-gold-light) 100%);
            color: var(--sincor-navy);
            padding: 10px 20px;
            border-radius: 24px;
            font-size: 13px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 1px;
            box-shadow: var(--shadow-gold);
            display: inline-block;
            border: 2px solid var(--sincor-gold-dark);
        }

        .badge-popular {
            background: linear-gradient(135deg, var(--sincor-navy) 0%, var(--sincor-navy-light) 100%);
            color: var(--white);
            padding: 10px 20px;
            border-radius: 24px;
            font-size: 13px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 1px;
            box-shadow: var(--shadow-md);
            display: inline-block;
            border: 2px solid var(--sincor-navy-dark);
        }

        .badge-value {
            background: linear-gradient(135deg, var(--sincor-orange) 0%, var(--sincor-orange-light) 100%);
            color: var(--white);
            padding: 10px 20px;
            border-radius: 24px;
            font-size: 13px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 1px;
            box-shadow: 0 4px 16px rgba(216, 119, 41, 0.3);
            display: inline-block;
            border: 2px solid var(--sincor-orange-dark);
        }

        /* Trust metrics - clean professional cards */
        .trust-metric-card {
            background: var(--white);
            border: 2px solid var(--gray-200);
            border-radius: 12px;
            padding: 24px;
            text-align: center;
            transition: all 0.3s ease;
            box-shadow: var(--shadow-sm);
        }

        .trust-metric-card:hover {
            border-color: var(--sincor-gold);
            box-shadow: var(--shadow-md);
            transform: translateY(-4px);
        }

        /* Button styles - navy and gold CTAs */
        .cta-cyan, .cta-gold, .cta-navy {
            border: none;
            border-radius: 8px;
            padding: 16px 32px;
            font-weight: 700;
            font-size: 16px;
            cursor: pointer;
            transition: all 0.3s ease;
            box-shadow: var(--shadow-md);
            position: relative;
            overflow: hidden;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .cta-cyan, .cta-navy {
            background: linear-gradient(135deg, var(--sincor-navy) 0%, var(--sincor-navy-light) 100%);
            color: var(--white);
            border: 2px solid var(--sincor-navy-dark);
        }

        .cta-cyan:hover, .cta-navy:hover {
            transform: translateY(-2px);
            box-shadow: var(--shadow-lg);
            background: linear-gradient(135deg, var(--sincor-navy-dark) 0%, var(--sincor-navy) 100%);
        }

        .cta-gold {
            background: linear-gradient(135deg, var(--sincor-gold) 0%, var(--sincor-gold-light) 100%);
            color: var(--sincor-navy);
            border: 2px solid var(--sincor-gold-dark);
        }

        .cta-gold:hover {
            transform: translateY(-2px);
            box-shadow: var(--shadow-gold);
            background: linear-gradient(135deg, var(--sincor-gold-light) 0%, var(--sincor-gold) 100%);
        }

        /* Price display - navy and gold gradient */
        .price-badge, .text-5xl.font-black {
            background: linear-gradient(135deg, var(--sincor-navy) 0%, var(--sincor-gold) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            font-weight: 900;
            font-size: 3.5rem;
            letter-spacing: -0.03em;
        }

        /* Example boxes - subtle corporate styling */
        .demo-box, .mt-4.p-4.bg-gradient-to-br {
            background: linear-gradient(135deg, var(--gray-50) 0%, var(--white) 100%);
            border: 2px solid var(--gray-200);
            border-left: 4px solid var(--sincor-gold);
            border-radius: 12px;
            padding: 20px;
            box-shadow: var(--shadow-sm);
            transition: all 0.3s ease;
        }

        .demo-box:hover, .mt-4.p-4.bg-gradient-to-br:hover {
            border-left-color: var(--sincor-orange);
            box-shadow: var(--shadow-md);
        }

        /* PayPal container styling */
        .paypal-container {
            min-height: 50px;
            padding: 12px 0;
            border-radius: 8px;
        }

        /* Icon styling */
        .product-icon {
            font-size: 3rem;
            margin-bottom: 16px;
            display: inline-block;
        }

        /* Typography - professional hierarchy */
        h1, h2, h3 {
            color: var(--sincor-navy);
            font-weight: 800;
            letter-spacing: -0.02em;
        }

        h1 {
            font-size: 3rem;
            line-height: 1.1;
        }

        h2 {
            font-size: 2.5rem;
            line-height: 1.2;
        }

        h3 {
            font-size: 1.75rem;
            line-height: 1.3;
        }

        /* Text colors - professional palette */
        .text-white {
            color: var(--gray-900);
        }

        .text-gray-600, .text-gray-700 {
            color: var(--gray-600);
        }

        .text-gray-800, .text-gray-900 {
            color: var(--sincor-navy);
        }

        /* Gold accents for emphasis */
        .text-gold {
            color: var(--sincor-gold);
        }

        /* Container and spacing */
        .container {
            max-width: 1280px;
            margin: 0 auto;
            padding: 0 24px;
        }

        .grid {
            display: grid;
            gap: 32px;
        }

        /* Premium scrollbar - navy and gold */
        ::-webkit-scrollbar {
            width: 12px;
        }

        ::-webkit-scrollbar-track {
            background: var(--cream);
        }

        ::-webkit-scrollbar-thumb {
            background: linear-gradient(135deg, var(--sincor-navy) 0%, var(--sincor-gold) 100%);
            border-radius: 6px;
        }

        ::-webkit-scrollbar-thumb:hover {
            background: linear-gradient(135deg, var(--sincor-navy-dark) 0%, var(--sincor-gold-light) 100%);
        }

        /* Feature highlights - gold accent */
        .feature-highlight {
            background: linear-gradient(135deg, rgba(212, 175, 55, 0.05) 0%, rgba(26, 40, 82, 0.03) 100%);
            border-left: 4px solid var(--sincor-gold);
            padding: 16px;
            border-radius: 8px;
            margin: 16px 0;
        }

        /* Stats counter - navy to gold gradient */
        .stat-number {
            font-size: 2.5rem;
            font-weight: 900;
            background: linear-gradient(135deg, var(--sincor-navy) 0%, var(--sincor-gold) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        /* Globe icon integration (matching logo) */
        .globe-icon {
            color: var(--sincor-gold);
            filter: drop-shadow(0 2px 4px rgba(212, 175, 55, 0.3));
        }

        /* Responsive design */
        @media (max-width: 768px) {
            .swarm-title {
                font-size: 2.5rem;
            }

            .product-card {
                padding: 24px;
            }

            h1 {
                font-size: 2rem;
            }

            h2 {
                font-size: 1.75rem;
            }

            .price-badge, .text-5xl.font-black {
                font-size: 2.5rem;
            }
        }

        /* Premium polish */
        .bg-white {
            background: var(--white);
        }

        .shadow-lg {
            box-shadow: var(--shadow-lg);
        }

        .shadow-xl {
            box-shadow: var(--shadow-xl);
        }

        /* Hover effects */
        button, a {
            cursor: pointer;
            transition: all 0.3s ease;
        }

        button:active {
            transform: scale(0.98);
        }

        /* Accessibility focus states - gold ring */
        button:focus, a:focus {
            outline: 3px solid var(--sincor-gold);
            outline-offset: 2px;
        }

        /* Premium dividers */
        hr {
            border: none;
            height: 2px;
            background: linear-gradient(90deg, transparent, var(--sincor-gold), transparent);
            margin: 40px 0;
        }

        /* Corporate badge styling */
        .corporate-badge {
            background: var(--sincor-navy);
            color: var(--sincor-gold);
            border: 2px solid var(--sincor-gold);
            padding: 4px 12px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 800;
            letter-spacing: 1.5px;
        }
    </style>'''

# Replace the old style section
content = content[:style_start_idx] + sincor_brand_css + content[style_end_idx:]

# Write the updated file
with open('templates/buy.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('SUCCESS: SINCOR brand-aligned design applied!')
print('Brand Colors Extracted from Logo:')
print('  - Navy Blue: #1a2852 (primary)')
print('  - Gold: #d4af37 (accent)')
print('  - Orange: #d87729 (highlight)')
print('Design Features:')
print('  - Premium corporate aesthetic matching logo')
print('  - Navy and gold color scheme')
print('  - Professional serif headings (Playfair Display)')
print('  - Clean white cards with subtle shadows')
print('  - Gold accent borders and badges')
print('  - Global enterprise feel')
print(f'File size: {len(content)} characters')
