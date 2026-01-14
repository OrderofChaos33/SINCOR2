/*
CORTEX Floating Chat Widget - Direct communication with your 42-agent ecosystem
*/

class CortexChatWidget {
    constructor() {
        this.isOpen = false;
        this.isMinimized = false;
        this.socket = null;
        this.messages = [];
        this.agentStatus = {};
        
        this.init();
    }
    
    init() {
        this.createWidget();
        this.initializeSocket();
        this.attachEventListeners();
        
        // Auto-show welcome message after 3 seconds
        setTimeout(() => {
            this.showWelcomeNotification();
        }, 3000);
    }
    
    createWidget() {
        // Create widget HTML
        const widgetHTML = `
        <div id="cortex-chat-widget" class="cortex-chat-widget">
            <!-- Chat Button -->
            <div id="cortex-chat-button" class="cortex-chat-button">
                <div class="chat-icon">
                    <span class="brain-icon">🧠</span>
                    <span class="notification-badge" id="notification-badge" style="display: none;">!</span>
                </div>
                <div class="chat-preview">
                    <div class="preview-text">💬 CHAT WITH CORTEX</div>
                    <div class="preview-subtext">Talk to 42 AI agents</div>
                </div>
            </div>
            
            <!-- Chat Window -->
            <div id="cortex-chat-window" class="cortex-chat-window" style="display: none;">
                <!-- Header -->
                <div class="chat-header">
                    <div class="header-left">
                        <div class="status-indicator online"></div>
                        <div class="header-info">
                            <div class="header-title">CORTEX</div>
                            <div class="header-subtitle">42-Agent Business AI</div>
                        </div>
                    </div>
                    <div class="header-controls">
                        <button id="minimize-btn" class="control-btn" title="Minimize">−</button>
                        <button id="close-btn" class="control-btn" title="Close">×</button>
                    </div>
                </div>
                
                <!-- Messages Area -->
                <div class="chat-messages" id="chat-messages">
                    <div class="system-message">
                        <div class="message-avatar">🤖</div>
                        <div class="message-content">
                            <strong>CORTEX activated!</strong><br>
                            I'm your AI business companion with 42 specialized agents ready to help.<br><br>
                            Try: "Get me 100 leads for my business" or "How's my performance this month?"
                        </div>
                    </div>
                </div>
                
                <!-- Agent Status Panel -->
                <div class="agent-status-panel" id="agent-status-panel" style="display: none;">
                    <div class="status-header">
                        <span>Agent Coordination</span>
                        <div class="coordination-score">100/100</div>
                    </div>
                    <div class="active-agents" id="active-agents">
                        No agents currently active
                    </div>
                </div>
                
                <!-- Input Area -->
                <div class="chat-input-area">
                    <div class="quick-actions">
                        <button class="quick-btn" data-message="Get me 100 leads">📈 Leads</button>
                        <button class="quick-btn" data-message="Business performance report">📊 Report</button>
                        <button class="quick-btn" data-message="Launch marketing campaign">🚀 Campaign</button>
                        <button id="agents-toggle" class="quick-btn">🤖 Agents</button>
                    </div>
                    <div class="input-container">
                        <input type="text" id="message-input" placeholder="Ask CORTEX anything..." maxlength="500">
                        <button id="send-btn" class="send-btn">
                            <span class="send-icon">➤</span>
                        </button>
                    </div>
                </div>
            </div>
        </div>
        `;
        
        // Create CSS styles
        const styles = `
        <style>
        .cortex-chat-widget {
            position: fixed;
            bottom: 20px;
            right: 20px;
            z-index: 10000;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }
        
        .cortex-chat-button {
            display: flex;
            align-items: center;
            background: linear-gradient(135deg, #00ff41 0%, #00aa00 100%);
            color: #000;
            padding: 15px 25px;
            border-radius: 50px;
            cursor: pointer;
            box-shadow: 0 6px 30px rgba(0, 255, 65, 0.5);
            transition: all 0.3s ease;
            border: 2px solid #00ff41;
            min-width: 80px;
            font-weight: bold;
        }
        
        .cortex-chat-button:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 25px rgba(0, 255, 65, 0.4);
        }
        
        .chat-icon {
            position: relative;
            margin-right: 12px;
        }
        
        .brain-icon {
            font-size: 24px;
            animation: pulse 2s infinite;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.7; }
        }
        
        .notification-badge {
            position: absolute;
            top: -5px;
            right: -5px;
            background: #ff4444;
            color: white;
            border-radius: 50%;
            width: 18px;
            height: 18px;
            font-size: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            animation: bounce 1s infinite;
        }
        
        @keyframes bounce {
            0%, 100% { transform: scale(1); }
            50% { transform: scale(1.2); }
        }
        
        .chat-preview {
            display: flex;
            flex-direction: column;
        }
        
        .preview-text {
            font-weight: bold;
            font-size: 14px;
        }
        
        .preview-subtext {
            font-size: 11px;
            opacity: 0.8;
        }
        
        .cortex-chat-window {
            position: absolute;
            bottom: 80px;
            right: 0;
            width: 420px;
            height: 650px;
            background: linear-gradient(135deg, #0a0a0a 0%, #1a1a2e 100%);
            border-radius: 12px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.5);
            border: 1px solid #00ff41;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            max-height: 90vh;
        }
        
        .chat-header {
            background: linear-gradient(90deg, #001a00 0%, #003300 100%);
            padding: 15px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #00ff41;
        }
        
        .header-left {
            display: flex;
            align-items: center;
        }
        
        .status-indicator {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 10px;
        }
        
        .status-indicator.online {
            background: #00ff41;
            box-shadow: 0 0 8px #00ff41;
        }
        
        .header-info {
            color: #00ff41;
        }
        
        .header-title {
            font-weight: bold;
            font-size: 16px;
        }
        
        .header-subtitle {
            font-size: 12px;
            opacity: 0.8;
        }
        
        .header-controls {
            display: flex;
            gap: 5px;
        }
        
        .control-btn {
            background: none;
            border: 1px solid #555;
            color: #aaa;
            width: 28px;
            height: 28px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.3s;
        }
        
        .control-btn:hover {
            border-color: #00ff41;
            color: #00ff41;
        }
        
        .chat-messages {
            flex: 1;
            overflow-y: auto;
            overflow-x: hidden;
            padding: 15px;
            scrollbar-width: thin;
            scrollbar-color: #00ff41 #1a1a1a;
            max-height: 400px;
            scroll-behavior: smooth;
        }
        
        .chat-messages::-webkit-scrollbar {
            width: 8px;
        }
        
        .chat-messages::-webkit-scrollbar-track {
            background: #1a1a1a;
            border-radius: 4px;
        }
        
        .chat-messages::-webkit-scrollbar-thumb {
            background: linear-gradient(45deg, #00ff41, #00aa00);
            border-radius: 4px;
            border: 1px solid #1a1a1a;
        }
        
        .chat-messages::-webkit-scrollbar-thumb:hover {
            background: linear-gradient(45deg, #00aa00, #008800);
        }
        
        .system-message, .user-message, .cortex-message {
            display: flex;
            margin-bottom: 15px;
            align-items: flex-start;
        }
        
        .message-avatar {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: #00ff41;
            color: #000;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-right: 10px;
            font-size: 16px;
            flex-shrink: 0;
        }
        
        .user-message .message-avatar {
            background: #0088ff;
            color: #fff;
        }
        
        .message-content {
            background: rgba(0, 255, 65, 0.1);
            padding: 10px 12px;
            border-radius: 12px;
            color: #00ff41;
            font-size: 14px;
            line-height: 1.4;
            border: 1px solid rgba(0, 255, 65, 0.3);
            flex: 1;
            word-wrap: break-word;
            overflow-wrap: break-word;
            white-space: pre-wrap;
            max-width: 280px;
        }
        
        .user-message .message-content {
            background: rgba(0, 136, 255, 0.1);
            color: #0088ff;
            border-color: rgba(0, 136, 255, 0.3);
        }
        
        .agent-status-panel {
            background: #111;
            border-top: 1px solid #333;
            padding: 10px;
            max-height: 120px;
            overflow-y: auto;
        }
        
        .status-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
            font-size: 12px;
            color: #00ff41;
        }
        
        .coordination-score {
            background: rgba(0, 255, 65, 0.2);
            padding: 2px 8px;
            border-radius: 10px;
            font-weight: bold;
        }
        
        .active-agents {
            font-size: 11px;
            color: #888;
        }
        
        .chat-input-area {
            padding: 10px;
            background: #0a0a0a;
            border-top: 1px solid #333;
        }
        
        .quick-actions {
            display: flex;
            gap: 5px;
            margin-bottom: 8px;
            flex-wrap: wrap;
        }
        
        .quick-btn {
            background: none;
            border: 1px solid #333;
            color: #888;
            padding: 4px 8px;
            border-radius: 12px;
            cursor: pointer;
            font-size: 10px;
            transition: all 0.3s;
        }
        
        .quick-btn:hover, .quick-btn.active {
            border-color: #00ff41;
            color: #00ff41;
        }
        
        .input-container {
            display: flex;
            gap: 8px;
        }
        
        #message-input {
            flex: 1;
            background: #1a1a1a;
            border: 1px solid #333;
            color: #00ff41;
            padding: 10px 12px;
            border-radius: 20px;
            font-size: 14px;
            outline: none;
        }
        
        #message-input:focus {
            border-color: #00ff41;
            box-shadow: 0 0 8px rgba(0, 255, 65, 0.3);
        }
        
        .send-btn {
            background: linear-gradient(45deg, #00ff41, #00aa00);
            border: none;
            color: #000;
            width: 40px;
            height: 40px;
            border-radius: 50%;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.3s;
        }
        
        .send-btn:hover {
            transform: scale(1.1);
        }
        
        .send-btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        
        .send-icon {
            font-size: 16px;
            font-weight: bold;
        }
        
        /* Responsive design */
        @media (max-width: 768px) {
            .cortex-chat-widget {
                bottom: 15px;
                right: 15px;
            }
            
            .cortex-chat-window {
                width: calc(100vw - 30px);
                height: calc(100vh - 120px);
                bottom: 70px;
                right: 15px;
                max-width: none;
            }
            
            .message-content {
                max-width: calc(100vw - 120px);
            }
            
            .chat-preview {
                display: none !important;
            }
            
            .cortex-chat-button {
                padding: 12px;
                min-width: 50px;
            }
        }
        
        @media (max-width: 480px) {
            .cortex-chat-window {
                width: calc(100vw - 20px);
                height: calc(100vh - 100px);
                bottom: 60px;
                right: 10px;
            }
            
            .message-content {
                max-width: calc(100vw - 100px);
                font-size: 13px;
            }
            
            .quick-actions {
                flex-wrap: wrap;
            }
            
            .quick-btn {
                font-size: 9px;
                padding: 3px 6px;
            }
        }
        
        /* Animations */
        .cortex-chat-window.opening {
            animation: slideUp 0.3s ease-out;
        }
        
        .cortex-chat-window.closing {
            animation: slideDown 0.3s ease-out;
        }
        
        @keyframes slideUp {
            from { transform: translateY(20px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }
        
        @keyframes slideDown {
            from { transform: translateY(0); opacity: 1; }
            to { transform: translateY(20px); opacity: 0; }
        }
        </style>
        `;
        
        // Add to page
        document.head.insertAdjacentHTML('beforeend', styles);
        document.body.insertAdjacentHTML('beforeend', widgetHTML);
    }
    
    initializeSocket() {
        // Initialize Socket.IO connection to CORTEX backend
        if (typeof io !== 'undefined') {
            this.socket = io('http://localhost:5001');
            
            this.socket.on('connect', () => {
                console.log('CORTEX widget connected');
                this.updateConnectionStatus(true);
            });
            
            this.socket.on('disconnect', () => {
                console.log('CORTEX widget disconnected');
                this.updateConnectionStatus(false);
            });
            
            this.socket.on('cortex_response', (data) => {
                this.addMessage('cortex', data.message);
                this.updateAgentStatus(data);
            });
            
            this.socket.on('agent_activation', (data) => {
                this.showAgentActivation(data);
            });
            
            this.socket.on('processing', (data) => {
                this.showProcessingMessage(data.message);
            });
        } else {
            console.warn('Socket.IO not loaded - CORTEX chat will work in demo mode');
        }
    }
    
    attachEventListeners() {
        const chatButton = document.getElementById('cortex-chat-button');
        const closeBtn = document.getElementById('close-btn');
        const minimizeBtn = document.getElementById('minimize-btn');
        const sendBtn = document.getElementById('send-btn');
        const messageInput = document.getElementById('message-input');
        const agentsToggle = document.getElementById('agents-toggle');
        
        chatButton.addEventListener('click', () => this.toggleChat());
        closeBtn.addEventListener('click', () => this.closeChat());
        minimizeBtn.addEventListener('click', () => this.minimizeChat());
        sendBtn.addEventListener('click', () => this.sendMessage());
        agentsToggle.addEventListener('click', () => this.toggleAgentPanel());
        
        messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') this.sendMessage();
        });
        
        // Quick action buttons
        document.querySelectorAll('.quick-btn[data-message]').forEach(btn => {
            btn.addEventListener('click', () => {
                const message = btn.dataset.message;
                this.sendMessage(message);
            });
        });
    }
    
    toggleChat() {
        const chatWindow = document.getElementById('cortex-chat-window');
        
        if (this.isOpen) {
            this.closeChat();
        } else {
            chatWindow.style.display = 'flex';
            chatWindow.classList.add('opening');
            this.isOpen = true;
            this.hideNotification();
            
            // Focus input
            setTimeout(() => {
                document.getElementById('message-input').focus();
            }, 300);
        }
    }
    
    closeChat() {
        const chatWindow = document.getElementById('cortex-chat-window');
        chatWindow.classList.add('closing');
        
        setTimeout(() => {
            chatWindow.style.display = 'none';
            chatWindow.classList.remove('opening', 'closing');
            this.isOpen = false;
        }, 300);
    }
    
    minimizeChat() {
        this.closeChat();
        this.showNotification();
    }
    
    showNotification() {
        document.getElementById('notification-badge').style.display = 'flex';
    }
    
    hideNotification() {
        document.getElementById('notification-badge').style.display = 'none';
    }
    
    showWelcomeNotification() {
        if (!this.isOpen) {
            this.showNotification();
            
            // Auto-hide after 10 seconds
            setTimeout(() => {
                if (!this.isOpen) this.hideNotification();
            }, 10000);
        }
    }
    
    sendMessage(message = null) {
        const messageInput = document.getElementById('message-input');
        const text = message || messageInput.value.trim();
        
        if (!text) return;
        
        // Add user message
        this.addMessage('user', text);
        messageInput.value = '';
        
        // Send to CORTEX
        if (this.socket) {
            this.socket.emit('cortex_message', { message: text });
        } else {
            // Demo mode response
            this.simulateCortexResponse(text);
        }
    }
    
    addMessage(type, content) {
        const messagesContainer = document.getElementById('chat-messages');
        const messageDiv = document.createElement('div');
        messageDiv.className = `${type}-message`;
        
        const avatar = type === 'user' ? '👤' : '🤖';
        
        messageDiv.innerHTML = `
            <div class="message-avatar">${avatar}</div>
            <div class="message-content">${content.replace(/\n/g, '<br>')}</div>
        `;
        
        messagesContainer.appendChild(messageDiv);
        
        // Force scroll to bottom with multiple methods
        setTimeout(() => {
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
            messagesContainer.scrollIntoView(false);
            
            // Alternative method
            const lastMessage = messagesContainer.lastElementChild;
            if (lastMessage) {
                lastMessage.scrollIntoView({ behavior: 'smooth', block: 'end' });
            }
        }, 100);
    }
    
    simulateCortexResponse(userMessage) {
        // Demo mode - simulate CORTEX response
        setTimeout(() => {
            const responses = [
                "Absolutely! I'm coordinating your agent team now...\n\n🎯 **MISSION ACTIVATED**\n- Business Intel Agent: Analyzing market\n- Sales Agent: Preparing outreach\n- Marketing Agent: Creating campaigns\n\n**Timeline:** Results in 15-30 minutes\n**Expected:** High-quality leads incoming!",
                "Perfect! Let me handle that for you.\n\n📊 **BUSINESS INTELLIGENCE REPORT**\n- Revenue Growth: +45% this month\n- New Leads: 127 qualified prospects\n- Conversion Rate: 23%\n\n**Full analysis:** Agents delivering comprehensive report...",
                "Great idea! Your marketing machine is activating.\n\n🚀 **CAMPAIGN DEPLOYMENT**\n- Content Gen Agent: Creating materials\n- Social Media Agent: Scheduling posts\n- Email Agent: Preparing sequences\n\n**Status:** Multi-channel campaign launching!"
            ];
            
            const response = responses[Math.floor(Math.random() * responses.length)];
            this.addMessage('cortex', response);
            
            // Show agent activation
            this.showAgentActivation({
                agents: ['Business Intel Agent', 'Marketing Agent', 'Sales Agent'],
                message: '3 agents coordinating your request...'
            });
        }, 1000);
    }
    
    showAgentActivation(data) {
        const agentPanel = document.getElementById('active-agents');
        agentPanel.innerHTML = `<strong>Active:</strong> ${data.agents.join(', ')}`;
        
        // Auto-show agent panel briefly
        const panel = document.getElementById('agent-status-panel');
        panel.style.display = 'block';
        
        setTimeout(() => {
            if (!document.getElementById('agents-toggle').classList.contains('active')) {
                panel.style.display = 'none';
            }
        }, 5000);
    }
    
    showProcessingMessage(message) {
        // You could add a typing indicator here
        console.log('Processing:', message);
    }
    
    toggleAgentPanel() {
        const panel = document.getElementById('agent-status-panel');
        const toggle = document.getElementById('agents-toggle');
        
        if (panel.style.display === 'none' || !panel.style.display) {
            panel.style.display = 'block';
            toggle.classList.add('active');
        } else {
            panel.style.display = 'none';
            toggle.classList.remove('active');
        }
    }
    
    updateConnectionStatus(connected) {
        const indicator = document.querySelector('.status-indicator');
        if (connected) {
            indicator.classList.add('online');
            indicator.classList.remove('offline');
        } else {
            indicator.classList.add('offline');
            indicator.classList.remove('online');
        }
    }
    
    updateAgentStatus(data) {
        if (data.agents_coordinated) {
            const panel = document.getElementById('active-agents');
            panel.innerHTML = `<strong>Coordinated:</strong> ${data.agents_coordinated} agents completed task`;
        }
    }
}

// Auto-initialize when page loads
document.addEventListener('DOMContentLoaded', () => {
    // Load Socket.IO if not already loaded
    if (typeof io === 'undefined') {
        const script = document.createElement('script');
        script.src = 'https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js';
        script.onload = () => {
            new CortexChatWidget();
        };
        document.head.appendChild(script);
    } else {
        new CortexChatWidget();
    }
});

// Fallback initialization
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        if (!window.cortexWidget) {
            window.cortexWidget = new CortexChatWidget();
        }
    });
} else {
    if (!window.cortexWidget) {
        window.cortexWidget = new CortexChatWidget();
    }
}