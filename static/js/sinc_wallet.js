/**
 * SINCOR wallet helper — injected provider (desktop / in-app browser) + WalletConnect (mobile).
 */
(function (global) {
  const BASE_CHAIN_ID = '0x2105';
  const BASE_CHAIN_NUM = 8453;
  const BASE_PARAMS = {
    chainId: BASE_CHAIN_ID,
    chainName: 'Base',
    nativeCurrency: { name: 'ETH', symbol: 'ETH', decimals: 18 },
    rpcUrls: ['https://mainnet.base.org'],
    blockExplorerUrls: ['https://basescan.org'],
  };

  let activeProvider = null;
  let wcProvider = null;
  let wcInitPromise = null;

  function isMobile() {
    return /Android|webOS|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent || '');
  }

  function getInjected() {
    const eth = global.ethereum;
    if (!eth) return null;
    if (Array.isArray(eth.providers) && eth.providers.length) {
      return eth.providers.find((p) => p.isMetaMask) || eth.providers[0];
    }
    return eth;
  }

  function hasInjected() {
    const p = getInjected();
    return !!(p && typeof p.request === 'function');
  }

  function dappUrl() {
    return global.location.origin + global.location.pathname + global.location.search;
  }

  function projectId() {
    return String(global.SINC_WC_PROJECT_ID || '').trim();
  }

  async function getWalletConnectProvider() {
    const pid = projectId();
    if (!pid) {
      throw new Error('WalletConnect not configured — use Open in MetaMask below.');
    }
    if (wcProvider) return wcProvider;
    if (!wcInitPromise) {
      wcInitPromise = (async () => {
        const { EthereumProvider } = await import(
          'https://esm.sh/@walletconnect/ethereum-provider@2.21.4'
        );
        wcProvider = await EthereumProvider.init({
          projectId: pid,
          chains: [BASE_CHAIN_NUM],
          showQrModal: true,
          metadata: {
            name: 'SINCOR',
            description: 'Buy SINC on Base',
            url: 'https://getsincor.com',
            icons: ['https://getsincor.com/static/sincor_nav_icon.png'],
          },
          rpcMap: { [BASE_CHAIN_NUM]: 'https://mainnet.base.org' },
        });
        wcProvider.on('disconnect', () => {
          if (activeProvider === wcProvider) activeProvider = null;
        });
        return wcProvider;
      })();
    }
    return wcInitPromise;
  }

  function getProvider() {
    if (activeProvider) return activeProvider;
    return hasInjected() ? getInjected() : null;
  }

  async function ensureBase(provider) {
    provider = provider || getProvider();
    if (!provider) throw new Error('Wallet not connected');
    const chainId = await provider.request({ method: 'eth_chainId' });
    if (chainId !== BASE_CHAIN_ID) {
      try {
        await provider.request({
          method: 'wallet_switchEthereumChain',
          params: [{ chainId: BASE_CHAIN_ID }],
        });
      } catch (e) {
        if (e && e.code === 4902) {
          await provider.request({
            method: 'wallet_addEthereumChain',
            params: [BASE_PARAMS],
          });
        } else {
          throw e;
        }
      }
    }
  }

  async function connectViaInjected() {
    const p = getInjected();
    if (!p) throw new Error('No wallet in this browser');
    activeProvider = p;
    await ensureBase(p);
    const accounts = await p.request({ method: 'eth_requestAccounts' });
    if (!accounts || !accounts[0]) throw new Error('No account returned');
    return { provider: p, account: accounts[0] };
  }

  async function connectViaWalletConnect() {
    const p = await getWalletConnectProvider();
    if (!p.session) await p.enable();
    activeProvider = p;
    await ensureBase(p);
    const accounts = await p.request({ method: 'eth_requestAccounts' });
    if (!accounts || !accounts[0]) throw new Error('WalletConnect: no account');
    return { provider: p, account: accounts[0] };
  }

  async function connect(opts) {
    const forceWC = opts && opts.forceWalletConnect;
    if (!forceWC && hasInjected()) return connectViaInjected();
    if (projectId()) return connectViaWalletConnect();
    throw new Error('No wallet here — tap Open in MetaMask or Coinbase Wallet below.');
  }

  async function getAccount() {
    const p = getProvider();
    if (!p) return null;
    const accounts = await p.request({ method: 'eth_accounts' });
    return accounts && accounts[0] ? accounts[0] : null;
  }

  function openMetaMask() {
    global.location.href = 'https://metamask.app.link/dapp/' + encodeURIComponent(dappUrl());
  }

  function openCoinbaseWallet() {
    global.location.href = 'https://go.cb-w.com/dapp?cb_url=' + encodeURIComponent(dappUrl());
  }

  function openRainbow() {
    global.location.href = 'https://rnbwapp.com/dapp?url=' + encodeURIComponent(dappUrl());
  }

  function shouldShowMobilePicker() {
    return isMobile() && !hasInjected();
  }

  function wirePicker(pickerId, onConnected) {
    const el = document.getElementById(pickerId);
    if (!el) return;
    if (shouldShowMobilePicker()) el.style.display = 'block';

    const mm = el.querySelector('[data-wallet="metamask"]');
    const cb = el.querySelector('[data-wallet="coinbase"]');
    const rb = el.querySelector('[data-wallet="rainbow"]');
    const wc = el.querySelector('[data-wallet="walletconnect"]');

    if (mm) mm.addEventListener('click', () => openMetaMask());
    if (cb) cb.addEventListener('click', () => openCoinbaseWallet());
    if (rb) rb.addEventListener('click', () => openRainbow());
    if (wc) {
      if (!projectId()) {
        wc.disabled = true;
        wc.title = 'Set WALLETCONNECT_PROJECT_ID on Railway';
        wc.style.opacity = '0.45';
      }
      wc.addEventListener('click', async () => {
        try {
          const { account } = await connectViaWalletConnect();
          if (typeof onConnected === 'function') onConnected(account);
        } catch (err) {
          console.warn('WalletConnect failed:', err);
        }
      });
    }
  }

  global.SincWallet = {
    BASE_CHAIN_ID,
    BASE_CHAIN_NUM,
    BASE_PARAMS,
    isMobile,
    hasInjected,
    getProvider,
    connect,
    connectViaInjected,
    connectViaWalletConnect,
    ensureBase,
    getAccount,
    openMetaMask,
    openCoinbaseWallet,
    openRainbow,
    shouldShowMobilePicker,
    wirePicker,
    projectId,
  };
})(window);