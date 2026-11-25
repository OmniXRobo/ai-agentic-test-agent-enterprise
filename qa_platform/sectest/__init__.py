"""23.14.0: AI Security & Vulnerability Testing.

``checks``   passive rules on captured HTTP exchanges (headers, cookies, CORS, TLS, disclosure, caching, forms)
``crawler``  read-only application discovery (pages, menus, forms, fields, uploads, login / logout, API endpoints,
             security headers); never submits a form or follows a logout / delete style link
``active``   approved active checks (reflected XSS, SQL error indicators, path traversal, open redirect, command
             injection indicators, XML entity handling, CORS reflection, authorization matrix, session checks)
``fuzz``     controlled, bounded fuzz testing of parameters, JSON and XML
``scenarios`` business-logic scenarios (replay, ownership, workflow / approval sequence, concurrent updates)
``zap``      the OWASP ZAP adapter (a locally installed ZAP daemon; passive, spider, approved active scan)
``service``  applications, approved application maps, assessments, findings, review, retest and regression
"""
