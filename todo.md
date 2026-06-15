# TODO

- [ ] Fix launch automatically and hide to system tray
- [ ] Add more bypass methods (e.g., wrong_checksum, wrong_ttl, TCP timestamp manipulation)
- [ ] Add authentication for proxies
- [ ] Bypass / Split Routing Rules: The app can exempt selected domains/IP categories from proxying.
  - When enabled:
    - Some domains/IP ranges go to `direct`
    - All other inbound traffic goes to `proxy`
  - This matters for:
    - Local/LAN reachability
    - Private IP ranges
    - Country/category bypass
    - Keeping routers and LAN devices accessible