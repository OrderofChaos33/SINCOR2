(function () {
  try {
    var paid = /sincor_plan=(starter|professional|enterprise|pro|member)/i.test(document.cookie)
      || document.cookie.indexOf('access_token=') !== -1
      || !!localStorage.getItem('sincor_plan');
    if (!paid) return;
    document.body.classList.add('is-member');
    document.querySelectorAll('[data-upsell], .upgrade-banner, .pricing-cta').forEach(function (el) { el.remove(); });
  } catch (e) {}
})();
