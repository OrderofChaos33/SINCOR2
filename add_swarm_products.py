#!/usr/bin/env python3
"""
Add SWARM Multi-Agent products alongside existing service products
Includes embedded video demos and PayPal subscriptions
"""

with open('templates/buy.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Find where to insert SWARM products (after Growth Engine product, before PayPal scripts)
insert_marker = '<script src="https://www.paypal.com/sdk/js'
insert_pos = content.find(insert_marker)

# Create SWARM products section
swarm_products_html = '''
    <!-- SWARM MULTI-AGENT PRODUCTS -->
    <div class="container mx-auto px-4 py-16">
        <div class="text-center mb-12">
            <h2 class="text-4xl font-black mb-4 swarm-title">🤖 SWARM Multi-Agent Subscriptions</h2>
            <p class="text-xl" style="color: var(--gray-600);">Deploy the 42-Agent Workforce - Real AI Automation</p>
        </div>

        <div class="grid md:grid-cols-2 gap-8 max-w-6xl mx-auto">

            <!-- SWARM Product 1: Scout Squad -->
            <div class="product-card">
                <div class="badge-popular mb-4">STARTER SWARM</div>
                <div class="product-icon">🔍</div>
                <h3 class="text-2xl font-bold mb-3" style="color: var(--sincor-navy);">Scout Agent Squad</h3>
                <p class="text-lg mb-4" style="color: var(--gray-600);">5 AI Scouts working 24/7 for your business</p>

                <!-- Video Demo -->
                <div class="mb-6" style="border-radius: 12px; overflow: hidden; border: 2px solid var(--sincor-gold);">
                    <video controls style="width: 100%; max-height: 300px;">
                        <source src="/static/videos/swarm-demo-1.mp4" type="video/mp4">
                        Your browser does not support video.
                    </video>
                </div>

                <div class="text-5xl font-black mb-4 price-badge">$497<span class="text-lg">/mo</span></div>

                <div class="mb-6">
                    <h4 class="font-bold mb-2" style="color: var(--sincor-navy);">Your 5-Agent Team:</h4>
                    <ul class="space-y-2 text-sm" style="color: var(--gray-700);">
                        <li>✓ E-Auriga-01 (Scout) - Lead generation & prospecting</li>
                        <li>✓ E-Vega-02 (Scout) - Market research & intelligence</li>
                        <li>✓ E-Sirius-03 (Scout) - Competitor monitoring</li>
                        <li>✓ E-Altair-04 (Scout) - Opportunity discovery</li>
                        <li>✓ E-Polaris-05 (Synthesizer) - Weekly reports & insights</li>
                    </ul>
                </div>

                <div class="mb-6">
                    <h4 class="font-bold mb-2" style="color: var(--sincor-navy);">What They Do:</h4>
                    <ul class="space-y-2 text-sm" style="color: var(--gray-700);">
                        <li>• Automated lead discovery (200+ leads/week)</li>
                        <li>• Real-time competitor tracking</li>
                        <li>• Market opportunity alerts</li>
                        <li>• Weekly strategic briefs</li>
                        <li>• 24/7 autonomous operation</li>
                    </ul>
                </div>

                <div class="space-y-3">
                    <div id="pp-scout-squad" class="paypal-container"></div>
                </div>
            </div>

            <!-- SWARM Product 2: Full Swarm License -->
            <div class="product-card">
                <div class="featured-badge mb-4">🔥 FULL SWARM</div>
                <div class="product-icon">🚀</div>
                <h3 class="text-2xl font-bold mb-3" style="color: var(--sincor-navy);">Complete 42-Agent Swarm</h3>
                <p class="text-lg mb-4" style="color: var(--gray-600);">Entire AI workforce at your command</p>

                <!-- Video Demo -->
                <div class="mb-6" style="border-radius: 12px; overflow: hidden; border: 2px solid var(--sincor-gold);">
                    <video controls style="width: 100%; max-height: 300px;">
                        <source src="/static/videos/swarm-demo-2.mp4" type="video/mp4">
                        Your browser does not support video.
                    </video>
                </div>

                <div class="text-5xl font-black mb-4 price-badge">$4,997<span class="text-lg">/mo</span></div>

                <div class="mb-6">
                    <h4 class="font-bold mb-2" style="color: var(--sincor-navy);">All 42 Agents Across 7 Archetypes:</h4>
                    <ul class="space-y-2 text-sm" style="color: var(--gray-700);">
                        <li>✓ 12 Scout Agents - Market intelligence & lead gen</li>
                        <li>✓ 8 Synthesizer Agents - Analysis & reporting</li>
                        <li>✓ 6 Builder Agents - Automation & infrastructure</li>
                        <li>✓ 5 Negotiator Agents - Sales & outreach</li>
                        <li>✓ 4 Caretaker Agents - Data management</li>
                        <li>✓ 4 Auditor Agents - Quality & compliance</li>
                        <li>✓ 3 Director Agents - Strategy & coordination</li>
                    </ul>
                </div>

                <div class="mb-6">
                    <h4 class="font-bold mb-2" style="color: var(--sincor-navy);">Swarm Capabilities:</h4>
                    <ul class="space-y-2 text-sm" style="color: var(--gray-700);">
                        <li>• Complete business automation</li>
                        <li>• Self-coordinating task allocation</li>
                        <li>• Autonomous decision-making</li>
                        <li>• Multi-tier memory system</li>
                        <li>• Real-time collaboration</li>
                        <li>• Continuous self-improvement</li>
                        <li>• White-label deployment option</li>
                    </ul>
                </div>

                <div class="space-y-3">
                    <div id="pp-full-swarm" class="paypal-container"></div>
                </div>
            </div>

            <!-- SWARM Product 3: Custom Squad Builder -->
            <div class="product-card">
                <div class="badge-value mb-4">CUSTOM SQUAD</div>
                <div class="product-icon">⚙️</div>
                <h3 class="text-2xl font-bold mb-3" style="color: var(--sincor-navy);">Custom Agent Squad</h3>
                <p class="text-lg mb-4" style="color: var(--gray-600);">Pick exactly which agents you need</p>

                <div class="text-5xl font-black mb-4 price-badge">$1,997<span class="text-lg">/mo</span></div>

                <div class="mb-6">
                    <h4 class="font-bold mb-2" style="color: var(--sincor-navy);">Choose Your Squad (15 agents):</h4>
                    <ul class="space-y-2 text-sm" style="color: var(--gray-700);">
                        <li>✓ Select from all 42 specialized agents</li>
                        <li>✓ Mix and match archetypes</li>
                        <li>✓ Tailored to your business needs</li>
                        <li>✓ Easy agent swapping (2x/month)</li>
                        <li>✓ Dedicated onboarding session</li>
                    </ul>
                </div>

                <div class="mb-6">
                    <h4 class="font-bold mb-2" style="color: var(--sincor-navy);">Example Configurations:</h4>
                    <div class="space-y-3">
                        <div class="demo-box p-3">
                            <div class="font-bold text-xs mb-1">Sales-Focused Squad:</div>
                            <div class="text-xs">8 Scouts + 4 Negotiators + 3 Synthesizers</div>
                        </div>
                        <div class="demo-box p-3">
                            <div class="font-bold text-xs mb-1">Content Creation Squad:</div>
                            <div class="text-xs">6 Builders + 5 Synthesizers + 4 Auditors</div>
                        </div>
                        <div class="demo-box p-3">
                            <div class="font-bold text-xs mb-1">Operations Squad:</div>
                            <div class="text-xs">5 Builders + 4 Caretakers + 3 Auditors + 3 Directors</div>
                        </div>
                    </div>
                </div>

                <div class="space-y-3">
                    <div id="pp-custom-squad" class="paypal-container"></div>
                </div>
            </div>

            <!-- SWARM Product 4: Enterprise Unlimited -->
            <div class="product-card">
                <div class="featured-badge mb-4">🏢 ENTERPRISE</div>
                <div class="product-icon">👑</div>
                <h3 class="text-2xl font-bold mb-3" style="color: var(--sincor-navy);">Enterprise Unlimited Swarm</h3>
                <p class="text-lg mb-4" style="color: var(--gray-600);">Unlimited agents, unlimited scale</p>

                <div class="text-5xl font-black mb-4 price-badge">$14,997<span class="text-lg">/mo</span></div>

                <div class="mb-6">
                    <h4 class="font-bold mb-2" style="color: var(--sincor-navy);">Enterprise Features:</h4>
                    <ul class="space-y-2 text-sm" style="color: var(--gray-700);">
                        <li>✓ Unlimited agent deployment</li>
                        <li>✓ Multi-location support</li>
                        <li>✓ White-label branding</li>
                        <li>✓ Custom agent development</li>
                        <li>✓ Dedicated account manager</li>
                        <li>✓ 24/7 priority support</li>
                        <li>✓ On-premise deployment option</li>
                        <li>✓ API access & integrations</li>
                        <li>✓ Custom workflow automation</li>
                        <li>✓ Quarterly strategy reviews</li>
                    </ul>
                </div>

                <div class="mb-6">
                    <h4 class="font-bold mb-2" style="color: var(--sincor-navy);">Ideal For:</h4>
                    <ul class="space-y-2 text-sm" style="color: var(--gray-700);">
                        <li>• Agencies managing multiple clients</li>
                        <li>• Enterprise teams (100+ employees)</li>
                        <li>• Multi-location businesses</li>
                        <li>• High-volume operations</li>
                    </ul>
                </div>

                <div class="space-y-3">
                    <div id="pp-enterprise-swarm" class="paypal-container"></div>
                </div>
            </div>

        </div>
    </div>

    '''

# Insert SWARM products before PayPal scripts
content = content[:insert_pos] + swarm_products_html + content[insert_pos:]

# Now add PayPal subscription buttons for the 4 SWARM products
# Find the end of existing PayPal button code
last_button_marker = "}).render('#pp-growth-engine');"
last_button_pos = content.find(last_button_marker) + len(last_button_marker)

swarm_paypal_buttons = '''

    // SWARM SUBSCRIPTION BUTTONS

    // Button 10: Scout Squad - $497/month subscription
    paypal.Buttons({
        style: { layout: 'vertical', color: 'blue', shape: 'rect', label: 'subscribe' },
        createSubscription: function(data, actions) {
            return actions.subscription.create({
                plan_id: 'P-SCOUT-SQUAD-497'  // Replace with actual PayPal plan ID
            });
        },
        onApprove: function(data, actions) {
            alert('Scout Squad subscription activated! Subscription ID: ' + data.subscriptionID);
            window.location.href = '/payment/success?product=scout-squad&amount=497&subscription=' + data.subscriptionID;
        },
        onError: function(err) {
            console.error('PayPal error:', err);
            alert('Subscription error occurred. Please try again.');
        }
    }).render('#pp-scout-squad');

    // Button 11: Full Swarm - $4,997/month subscription
    paypal.Buttons({
        style: { layout: 'vertical', color: 'gold', shape: 'rect', label: 'subscribe' },
        createSubscription: function(data, actions) {
            return actions.subscription.create({
                plan_id: 'P-FULL-SWARM-4997'  // Replace with actual PayPal plan ID
            });
        },
        onApprove: function(data, actions) {
            alert('Full Swarm activated! All 42 agents deploying. Subscription ID: ' + data.subscriptionID);
            window.location.href = '/payment/success?product=full-swarm&amount=4997&subscription=' + data.subscriptionID;
        },
        onError: function(err) {
            console.error('PayPal error:', err);
            alert('Subscription error occurred. Please try again.');
        }
    }).render('#pp-full-swarm');

    // Button 12: Custom Squad - $1,997/month subscription
    paypal.Buttons({
        style: { layout: 'vertical', color: 'blue', shape: 'rect', label: 'subscribe' },
        createSubscription: function(data, actions) {
            return actions.subscription.create({
                plan_id: 'P-CUSTOM-SQUAD-1997'  // Replace with actual PayPal plan ID
            });
        },
        onApprove: function(data, actions) {
            alert('Custom Squad subscription activated! Subscription ID: ' + data.subscriptionID);
            window.location.href = '/payment/success?product=custom-squad&amount=1997&subscription=' + data.subscriptionID;
        },
        onError: function(err) {
            console.error('PayPal error:', err);
            alert('Subscription error occurred. Please try again.');
        }
    }).render('#pp-custom-squad');

    // Button 13: Enterprise Swarm - $14,997/month subscription
    paypal.Buttons({
        style: { layout: 'vertical', color: 'gold', shape: 'rect', label: 'subscribe' },
        createSubscription: function(data, actions) {
            return actions.subscription.create({
                plan_id: 'P-ENTERPRISE-SWARM-14997'  // Replace with actual PayPal plan ID
            });
        },
        onApprove: function(data, actions) {
            alert('Enterprise Swarm activated! Unlimited agents deploying. Subscription ID: ' + data.subscriptionID);
            window.location.href = '/payment/success?product=enterprise-swarm&amount=14997&subscription=' + data.subscriptionID;
        },
        onError: function(err) {
            console.error('PayPal error:', err);
            alert('Subscription error occurred. Please try again.');
        }
    }).render('#pp-enterprise-swarm');
'''

# Insert SWARM PayPal buttons after existing buttons
content = content[:last_button_pos] + swarm_paypal_buttons + content[last_button_pos:]

# Write updated file
with open('templates/buy.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('SUCCESS: SWARM Multi-Agent products added!')
print('\nAdded 4 SWARM Subscription Products:')
print('  1. Scout Squad (5 agents) - $497/month')
print('  2. Full 42-Agent Swarm - $4,997/month')
print('  3. Custom Squad (15 agents) - $1,997/month')
print('  4. Enterprise Unlimited - $14,997/month')
print('\nFeatures:')
print('  ✓ Embedded video demos in products 1 & 2')
print('  ✓ Real agent names from your agent files')
print('  ✓ PayPal subscription buttons (need plan IDs)')
print('  ✓ 7 archetype breakdown shown')
print('  ✓ Swarm coordination explained')
print(f'\nFile size: {len(content)} characters')
print('\nTotal products now: 9 services + 4 SWARM subscriptions = 13 products!')
