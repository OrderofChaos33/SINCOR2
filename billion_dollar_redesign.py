#!/usr/bin/env python3
"""
Transform SINCOR buy page into a billion-dollar looking website
Keep all content, upgrade visual design to ultra-premium
"""

with open('templates/buy.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Find and replace the <style> section with billion-dollar CSS
old_style_start = '    <style>'
old_style_end = '    </style>'

style_start_idx = content.find(old_style_start)
style_end_idx = content.find(old_style_end) + len(old_style_end)

# New billion-dollar CSS
billion_dollar_css = '''    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

        :root {
            --navy-dark: #0a0f2c;
            --navy: #1a1f3a;
            --navy-light: #2d3348;
            --gold: #fbbf24;
            --gold-light: #fcd34d;
            --gold-dark: #f59e0b;
            --cyan: #06b6d4;
            --cyan-light: #22d3ee;
            --cyan-dark: #0891b2;
            --purple: #8b5cf6;
            --purple-light: #a78bfa;
        }

        * {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }

        body {
            background: linear-gradient(135deg, #0a0f2c 0%, #1a1f3a 50%, #0f172a 100%);
            background-attachment: fixed;
            margin: 0;
            padding: 0;
            overflow-x: hidden;
        }

        /* Animated background particles */
        body::before {
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-image:
                radial-gradient(circle at 20% 30%, rgba(139, 92, 246, 0.15) 0%, transparent 50%),
                radial-gradient(circle at 80% 70%, rgba(6, 182, 212, 0.15) 0%, transparent 50%),
                radial-gradient(circle at 40% 80%, rgba(251, 191, 36, 0.1) 0%, transparent 50%);
            animation: float 20s ease-in-out infinite;
            z-index: 0;
            pointer-events: none;
        }

        @keyframes float {
            0%, 100% { opacity: 0.6; transform: translateY(0px); }
            50% { opacity: 1; transform: translateY(-20px); }
        }

        /* Premium header with glassmorphism */
        .premium-header {
            background: rgba(10, 15, 44, 0.8);
            backdrop-filter: blur(20px) saturate(180%);
            border-bottom: 1px solid rgba(251, 191, 36, 0.2);
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.3);
            position: relative;
            z-index: 100;
        }

        .premium-header::after {
            content: '';
            position: absolute;
            bottom: -1px;
            left: 0;
            right: 0;
            height: 1px;
            background: linear-gradient(90deg, transparent, var(--gold), transparent);
            opacity: 0.5;
        }

        /* Hero section - billion dollar look */
        .hero-section {
            background: linear-gradient(180deg, rgba(10, 15, 44, 0.95) 0%, rgba(26, 31, 58, 0.8) 100%);
            position: relative;
            overflow: hidden;
            border-bottom: 1px solid rgba(251, 191, 36, 0.1);
        }

        .hero-section::before {
            content: '';
            position: absolute;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: radial-gradient(circle, rgba(139, 92, 246, 0.1) 0%, transparent 70%);
            animation: rotate 30s linear infinite;
        }

        @keyframes rotate {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }

        .swarm-title {
            background: linear-gradient(135deg, var(--cyan) 0%, var(--purple-light) 50%, var(--gold-light) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            font-weight: 900;
            letter-spacing: -0.04em;
            text-shadow: 0 0 80px rgba(139, 92, 246, 0.5);
            animation: shimmer 3s ease-in-out infinite;
        }

        @keyframes shimmer {
            0%, 100% { filter: brightness(1) saturate(1); }
            50% { filter: brightness(1.2) saturate(1.3); }
        }

        /* Trust metrics with premium glass cards */
        .trust-metric-card {
            background: rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 16px;
            padding: 24px;
            transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
        }

        .trust-metric-card:hover {
            background: rgba(255, 255, 255, 0.08);
            border-color: rgba(251, 191, 36, 0.3);
            transform: translateY(-4px) scale(1.02);
            box-shadow: 0 20px 40px rgba(251, 191, 36, 0.2);
        }

        /* Product cards - ultra premium */
        .product-card {
            background: linear-gradient(135deg, rgba(255, 255, 255, 0.1) 0%, rgba(255, 255, 255, 0.05) 100%);
            backdrop-filter: blur(20px) saturate(180%);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 24px;
            padding: 40px;
            position: relative;
            overflow: hidden;
            transition: all 0.6s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            box-shadow:
                0 10px 40px rgba(0, 0, 0, 0.3),
                inset 0 1px 0 rgba(255, 255, 255, 0.1);
        }

        .product-card::before {
            content: '';
            position: absolute;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: radial-gradient(circle, rgba(6, 182, 212, 0.15) 0%, transparent 70%);
            opacity: 0;
            transition: opacity 0.6s ease;
        }

        .product-card:hover {
            transform: translateY(-24px) scale(1.02);
            border-color: rgba(6, 182, 212, 0.5);
            box-shadow:
                0 40px 80px rgba(6, 182, 212, 0.3),
                0 0 0 1px rgba(6, 182, 212, 0.2),
                inset 0 1px 0 rgba(255, 255, 255, 0.2);
        }

        .product-card:hover::before {
            opacity: 1;
        }

        /* Featured badge with premium glow */
        .featured-badge {
            background: linear-gradient(135deg, var(--gold) 0%, var(--gold-light) 100%);
            box-shadow:
                0 4px 20px rgba(251, 191, 36, 0.6),
                0 0 40px rgba(251, 191, 36, 0.4),
                inset 0 1px 0 rgba(255, 255, 255, 0.3);
            animation: glow-pulse 2s ease-in-out infinite;
            position: relative;
            overflow: hidden;
        }

        .featured-badge::before {
            content: '';
            position: absolute;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: linear-gradient(45deg, transparent, rgba(255, 255, 255, 0.3), transparent);
            transform: rotate(45deg);
            animation: shine 3s infinite;
        }

        @keyframes glow-pulse {
            0%, 100% {
                box-shadow:
                    0 4px 20px rgba(251, 191, 36, 0.6),
                    0 0 40px rgba(251, 191, 36, 0.4),
                    inset 0 1px 0 rgba(255, 255, 255, 0.3);
            }
            50% {
                box-shadow:
                    0 6px 30px rgba(251, 191, 36, 0.8),
                    0 0 60px rgba(251, 191, 36, 0.6),
                    inset 0 1px 0 rgba(255, 255, 255, 0.4);
            }
        }

        @keyframes shine {
            0% { transform: translateX(-100%) translateY(-100%) rotate(45deg); }
            100% { transform: translateX(100%) translateY(100%) rotate(45deg); }
        }

        /* Premium buttons */
        .cta-cyan, .cta-gold, .cta-navy {
            position: relative;
            overflow: hidden;
            border: none;
            font-weight: 700;
            letter-spacing: 0.5px;
            text-transform: uppercase;
            font-size: 14px;
            transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
        }

        .cta-cyan::before, .cta-gold::before, .cta-navy::before {
            content: '';
            position: absolute;
            top: 50%;
            left: 50%;
            width: 0;
            height: 0;
            border-radius: 50%;
            background: rgba(255, 255, 255, 0.3);
            transform: translate(-50%, -50%);
            transition: width 0.6s, height 0.6s;
        }

        .cta-cyan:hover::before, .cta-gold:hover::before, .cta-navy:hover::before {
            width: 400px;
            height: 400px;
        }

        .cta-cyan {
            background: linear-gradient(135deg, var(--cyan-dark) 0%, var(--cyan) 50%, var(--cyan-light) 100%);
            box-shadow: 0 10px 30px rgba(6, 182, 212, 0.4);
        }

        .cta-cyan:hover {
            transform: translateY(-2px);
            box-shadow: 0 15px 40px rgba(6, 182, 212, 0.6);
        }

        .cta-gold {
            background: linear-gradient(135deg, var(--gold-dark) 0%, var(--gold) 50%, var(--gold-light) 100%);
            box-shadow: 0 10px 30px rgba(251, 191, 36, 0.4);
        }

        .cta-gold:hover {
            transform: translateY(-2px);
            box-shadow: 0 15px 40px rgba(251, 191, 36, 0.6);
        }

        .cta-navy {
            background: linear-gradient(135deg, var(--navy) 0%, var(--navy-light) 50%, rgba(139, 92, 246, 0.5) 100%);
            box-shadow: 0 10px 30px rgba(139, 92, 246, 0.4);
        }

        .cta-navy:hover {
            transform: translateY(-2px);
            box-shadow: 0 15px 40px rgba(139, 92, 246, 0.6);
        }

        /* Demo boxes with premium glass effect */
        .demo-box {
            background: linear-gradient(135deg, rgba(6, 182, 212, 0.1) 0%, rgba(139, 92, 246, 0.1) 100%);
            backdrop-filter: blur(10px);
            border-left: 4px solid var(--cyan);
            border-radius: 12px;
            box-shadow:
                inset 0 1px 0 rgba(255, 255, 255, 0.1),
                0 4px 20px rgba(0, 0, 0, 0.1);
        }

        /* Example content boxes - ultra premium */
        .mt-4.p-4.bg-gradient-to-br {
            background: linear-gradient(135deg, rgba(255, 255, 255, 0.08) 0%, rgba(255, 255, 255, 0.03) 100%);
            backdrop-filter: blur(15px);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 16px;
            box-shadow:
                0 10px 30px rgba(0, 0, 0, 0.3),
                inset 0 1px 0 rgba(255, 255, 255, 0.1);
            transition: all 0.4s ease;
        }

        .mt-4.p-4.bg-gradient-to-br:hover {
            border-color: rgba(251, 191, 36, 0.3);
            box-shadow:
                0 15px 40px rgba(0, 0, 0, 0.4),
                inset 0 1px 0 rgba(255, 255, 255, 0.2);
        }

        /* Price badges - luxury styling */
        .price-badge, .text-5xl.font-black {
            background: linear-gradient(135deg, var(--cyan) 0%, var(--purple-light) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            font-weight: 900;
            letter-spacing: -0.04em;
            filter: drop-shadow(0 0 20px rgba(6, 182, 212, 0.5));
        }

        /* PayPal container */
        .paypal-container {
            min-height: 50px;
            padding: 12px 0;
            position: relative;
            z-index: 10;
        }

        /* Premium scrollbar */
        ::-webkit-scrollbar {
            width: 12px;
        }

        ::-webkit-scrollbar-track {
            background: rgba(10, 15, 44, 0.5);
        }

        ::-webkit-scrollbar-thumb {
            background: linear-gradient(135deg, var(--cyan) 0%, var(--purple) 100%);
            border-radius: 6px;
        }

        ::-webkit-scrollbar-thumb:hover {
            background: linear-gradient(135deg, var(--cyan-light) 0%, var(--purple-light) 100%);
        }

        /* Typography enhancements */
        h1, h2, h3 {
            font-weight: 800;
            letter-spacing: -0.03em;
        }

        /* Smooth transitions globally */
        * {
            transition: opacity 0.2s ease, transform 0.2s ease;
        }

        /* Premium text colors */
        .text-white {
            color: rgba(255, 255, 255, 0.95);
        }

        .text-gray-600 {
            color: rgba(255, 255, 255, 0.6);
        }

        .text-gray-700 {
            color: rgba(255, 255, 255, 0.7);
        }

        .text-gray-800 {
            color: rgba(255, 255, 255, 0.8);
        }

        .text-gray-900 {
            color: rgba(255, 255, 255, 0.9);
        }

        /* Glass effect for white backgrounds */
        .bg-white {
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(10px);
        }

        /* Premium shadows */
        .shadow-lg {
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
        }

        .shadow-xl {
            box-shadow: 0 30px 80px rgba(0, 0, 0, 0.4);
        }

        /* Responsive enhancements */
        @media (max-width: 768px) {
            .product-card {
                padding: 24px;
            }

            .price-badge {
                font-size: 2.5rem;
            }
        }
    </style>'''

# Replace the old style section
content = content[:style_start_idx] + billion_dollar_css + content[style_end_idx:]

# Write the updated file
with open('templates/buy.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('SUCCESS: Billion-dollar design applied!')
print('Features:')
print('  ✓ Dark luxury theme with glassmorphism')
print('  ✓ Animated background particles')
print('  ✓ Premium glass cards with blur effects')
print('  ✓ Micro-interactions and smooth animations')
print('  ✓ Gradient text with glow effects')
print('  ✓ Ultra-premium shadows and depth')
print('  ✓ Luxury brand aesthetics')
print('  ✓ Professional color grading')
print('  ✓ All content preserved exactly as-is')
print(f'\nFile size: {len(content)} characters')
