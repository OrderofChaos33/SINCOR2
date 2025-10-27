#!/usr/bin/env python3
"""
Complete remodel - Modern premium light theme
Apple/Stripe-inspired with white cards, vibrant gradients, clean spacing
"""

with open('templates/buy.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Find and replace the <style> section
old_style_start = '    <style>'
old_style_end = '    </style>'

style_start_idx = content.find(old_style_start)
style_end_idx = content.find(old_style_end) + len(old_style_end)

# New premium light remodel CSS
premium_light_css = '''    <style>
        @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800;900&display=swap');

        :root {
            --primary-blue: #0066FF;
            --primary-purple: #7C3AED;
            --primary-pink: #EC4899;
            --primary-orange: #F97316;
            --success-green: #10B981;
            --background: #F9FAFB;
            --surface: #FFFFFF;
            --border: #E5E7EB;
            --text-primary: #111827;
            --text-secondary: #6B7280;
            --shadow-sm: 0 1px 3px rgba(0,0,0,0.05);
            --shadow-md: 0 4px 12px rgba(0,0,0,0.08);
            --shadow-lg: 0 10px 40px rgba(0,0,0,0.1);
            --shadow-xl: 0 20px 60px rgba(0,0,0,0.12);
        }

        * {
            font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            background: linear-gradient(180deg, #F9FAFB 0%, #FFFFFF 100%);
            color: var(--text-primary);
            line-height: 1.6;
            overflow-x: hidden;
        }

        /* Vibrant gradient background accents */
        body::before {
            content: '';
            position: fixed;
            top: -50%;
            right: -20%;
            width: 80%;
            height: 80%;
            background: radial-gradient(circle, rgba(124, 58, 237, 0.08) 0%, transparent 70%);
            pointer-events: none;
            z-index: 0;
        }

        body::after {
            content: '';
            position: fixed;
            bottom: -30%;
            left: -10%;
            width: 60%;
            height: 60%;
            background: radial-gradient(circle, rgba(236, 72, 153, 0.06) 0%, transparent 70%);
            pointer-events: none;
            z-index: 0;
        }

        /* Premium header - clean and minimal */
        .premium-header {
            background: rgba(255, 255, 255, 0.8);
            backdrop-filter: blur(20px) saturate(180%);
            border-bottom: 1px solid var(--border);
            box-shadow: var(--shadow-sm);
            position: sticky;
            top: 0;
            z-index: 1000;
        }

        /* Hero section - bright and inviting */
        .hero-section {
            background: linear-gradient(135deg, #FFFFFF 0%, #F9FAFB 100%);
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
                radial-gradient(circle at 30% 20%, rgba(124, 58, 237, 0.1) 0%, transparent 50%),
                radial-gradient(circle at 70% 60%, rgba(0, 102, 255, 0.08) 0%, transparent 50%);
            z-index: 0;
        }

        .swarm-title {
            background: linear-gradient(135deg, var(--primary-blue) 0%, var(--primary-purple) 50%, var(--primary-pink) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            font-weight: 900;
            font-size: 4rem;
            letter-spacing: -0.04em;
            line-height: 1.1;
            position: relative;
            z-index: 1;
        }

        /* Product cards - modern card design */
        .product-card {
            background: var(--surface);
            border: 2px solid var(--border);
            border-radius: 20px;
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
            height: 6px;
            background: linear-gradient(90deg, var(--primary-blue), var(--primary-purple), var(--primary-pink));
            opacity: 0;
            transition: opacity 0.4s ease;
        }

        .product-card:hover {
            transform: translateY(-12px);
            border-color: var(--primary-blue);
            box-shadow: var(--shadow-xl);
        }

        .product-card:hover::before {
            opacity: 1;
        }

        /* Badge styles - vibrant and modern */
        .featured-badge {
            background: linear-gradient(135deg, #FFD700 0%, #FFA500 100%);
            color: #1F2937;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            box-shadow: 0 4px 12px rgba(255, 215, 0, 0.3);
            display: inline-block;
        }

        .badge-popular {
            background: linear-gradient(135deg, var(--primary-purple) 0%, var(--primary-pink) 100%);
            color: white;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            box-shadow: 0 4px 12px rgba(124, 58, 237, 0.3);
            display: inline-block;
        }

        .badge-value {
            background: linear-gradient(135deg, var(--success-green) 0%, #059669 100%);
            color: white;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            box-shadow: 0 4px 12px rgba(16, 185, 129, 0.3);
            display: inline-block;
        }

        /* Trust metrics - clean cards */
        .trust-metric-card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 24px;
            text-align: center;
            transition: all 0.3s ease;
            box-shadow: var(--shadow-sm);
        }

        .trust-metric-card:hover {
            border-color: var(--primary-blue);
            box-shadow: var(--shadow-md);
            transform: translateY(-4px);
        }

        /* Button styles - vibrant CTAs */
        .cta-cyan, .cta-gold, .cta-navy {
            border: none;
            border-radius: 12px;
            padding: 16px 32px;
            font-weight: 700;
            font-size: 16px;
            cursor: pointer;
            transition: all 0.3s ease;
            box-shadow: var(--shadow-md);
            position: relative;
            overflow: hidden;
        }

        .cta-cyan {
            background: linear-gradient(135deg, var(--primary-blue) 0%, #0052CC 100%);
            color: white;
        }

        .cta-cyan:hover {
            transform: translateY(-2px);
            box-shadow: 0 12px 24px rgba(0, 102, 255, 0.3);
        }

        .cta-gold {
            background: linear-gradient(135deg, var(--primary-orange) 0%, #EA580C 100%);
            color: white;
        }

        .cta-gold:hover {
            transform: translateY(-2px);
            box-shadow: 0 12px 24px rgba(249, 115, 22, 0.3);
        }

        .cta-navy {
            background: linear-gradient(135deg, var(--primary-purple) 0%, #6D28D9 100%);
            color: white;
        }

        .cta-navy:hover {
            transform: translateY(-2px);
            box-shadow: 0 12px 24px rgba(124, 58, 237, 0.3);
        }

        /* Price display - bold and clear */
        .price-badge, .text-5xl.font-black {
            background: linear-gradient(135deg, var(--primary-blue) 0%, var(--primary-purple) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            font-weight: 900;
            font-size: 3.5rem;
            letter-spacing: -0.03em;
        }

        /* Example boxes - light and airy */
        .demo-box, .mt-4.p-4.bg-gradient-to-br {
            background: linear-gradient(135deg, #F3F4F6 0%, #FFFFFF 100%);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            box-shadow: var(--shadow-sm);
            transition: all 0.3s ease;
        }

        .demo-box:hover, .mt-4.p-4.bg-gradient-to-br:hover {
            border-color: var(--primary-blue);
            box-shadow: var(--shadow-md);
        }

        /* PayPal container styling */
        .paypal-container {
            min-height: 50px;
            padding: 12px 0;
            border-radius: 8px;
        }

        /* Icon styling for product cards */
        .product-icon {
            font-size: 3rem;
            margin-bottom: 16px;
            display: inline-block;
        }

        /* Section headers */
        h1, h2, h3 {
            color: var(--text-primary);
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

        /* Text colors */
        .text-white {
            color: var(--text-primary);
        }

        .text-gray-600, .text-gray-700, .text-gray-800, .text-gray-900 {
            color: var(--text-secondary);
        }

        /* Spacing utilities */
        .container {
            max-width: 1280px;
            margin: 0 auto;
            padding: 0 24px;
        }

        /* Grid layouts */
        .grid {
            display: grid;
            gap: 32px;
        }

        /* Smooth scrollbar */
        ::-webkit-scrollbar {
            width: 12px;
        }

        ::-webkit-scrollbar-track {
            background: var(--background);
        }

        ::-webkit-scrollbar-thumb {
            background: linear-gradient(135deg, var(--primary-blue) 0%, var(--primary-purple) 100%);
            border-radius: 6px;
        }

        ::-webkit-scrollbar-thumb:hover {
            background: linear-gradient(135deg, #0052CC 0%, #6D28D9 100%);
        }

        /* Feature highlights */
        .feature-highlight {
            background: linear-gradient(135deg, rgba(0, 102, 255, 0.05) 0%, rgba(124, 58, 237, 0.05) 100%);
            border-left: 4px solid var(--primary-blue);
            padding: 16px;
            border-radius: 8px;
            margin: 16px 0;
        }

        /* Stats counter styling */
        .stat-number {
            font-size: 2.5rem;
            font-weight: 900;
            background: linear-gradient(135deg, var(--primary-blue) 0%, var(--primary-purple) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
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
            background: var(--surface);
        }

        .shadow-lg {
            box-shadow: var(--shadow-lg);
        }

        .shadow-xl {
            box-shadow: var(--shadow-xl);
        }

        /* Hover effects for interactive elements */
        button, a {
            cursor: pointer;
            transition: all 0.3s ease;
        }

        button:active {
            transform: scale(0.98);
        }

        /* Focus states for accessibility */
        button:focus, a:focus {
            outline: 3px solid rgba(0, 102, 255, 0.3);
            outline-offset: 2px;
        }
    </style>'''

# Replace the old style section
content = content[:style_start_idx] + premium_light_css + content[style_end_idx:]

# Write the updated file
with open('templates/buy.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('SUCCESS: Premium light remodel applied!')
print('New Design Features:')
print('  - Bright, modern light theme (white backgrounds)')
print('  - Plus Jakarta Sans premium typography')
print('  - Vibrant blue/purple/pink gradients')
print('  - Clean card design with subtle shadows')
print('  - Apple/Stripe-inspired aesthetic')
print('  - Colorful badge system')
print('  - Smooth hover interactions')
print('  - Accessible focus states')
print('  - Mobile responsive')
print(f'File size: {len(content)} characters')
