# Patch 23.3.24: Record performance scripts from a browser

Cumulative patch for 23.2.2 and 23.3.0 to 23.3.23. This is milestone 3 of 8 of the JMeter performance-testing
module. A performance script can now be recorded from a real browser journey, with session tokens handled
automatically, or imported from a HAR file.

## What is new

**Record a browser journey** (Performance Test Plans, and the load / stress / spike / soak pages)

* Start recording opens a browser window on this PC (Chromium, Microsoft Edge or Google Chrome) at the start URL.
  Use the application as a user would. No proxy and no certificate are installed: the browser reports its own
  network requests to the recorder.
* Steps: before each action you want measured on its own, type its name (for example "Log in", "Search") and press
  New step. With "Start a new step on each page navigation" a step also starts on each new page. Each step becomes a
  JMeter Transaction Controller, so the results show the time of the whole step as well as each request.
* Think time: the pause between steps is kept as a constant timer before the step's first request.
* Stop recording (or close the browser window), then review: which requests are kept, which were left out and why,
  which values are handled automatically, and which secrets were found. Untick any request to leave it out, change
  the hosts to keep, then Create script. Discard drops the recording and everything typed in it.

**What is left out**

* Requests to hosts that are not in "Hosts to keep" (by default the start URL's host): analytics, fonts, ads, maps.
* Static files (images, styles, scripts, fonts, media), unless "Keep images, styles, scripts and fonts" is ticked.
  Browsers cache these, so they are usually not part of a load test.
* CORS preflight (OPTIONS) requests, websockets and event streams, and requests that failed in the browser without
  a response.

**Token handling (correlation)**

* When a request sends back a value that an earlier kept response handed out (a hidden CSRF field, a JSON access
  token, a session or order id), the value is extracted from that response and replaced by a variable in every
  later request. JSON responses get a JSON extractor (`$.data.access_token`); other responses get a Boundary
  extractor whose left and right text is checked to find exactly that value in the recorded response.
* `Authorization: Bearer ...` headers are correlated the same way. Other Authorization headers (Basic) are secrets.
* Values that look dynamic but are not found in an earlier response (made by the page's JavaScript, or typed) are
  listed under "Check these" and are sent as recorded. They are never guessed.

**Secrets**

* Passwords typed into password fields are never written into the script. They become `${__P(password)}` and the
  value is saved in the Windows Credential Manager, under the script.
* When a test runs, the values are written to a properties file in the run folder that JMeter reads with `-q`; the
  file is deleted as soon as JMeter ends. It is never part of the run's snapshot, results or logs.
* The script editor has a Secrets section: which secrets the script uses, whether a value is saved, and a field to
  enter or replace one. Values are never shown. A missing value fails the pre-run check, with the fix.
* Cloning a scenario copies its secrets to the copy.

**Import a HAR file**

* Save the HAR from the browser's developer tools (Network tab, "Save all as HAR") and import it with the hosts to
  keep. The same filtering and token handling apply, and each page of the HAR becomes a step. A HAR does not say
  which field was a password, so check the script for passwords.

**Load**

* A recorded script starts with 1 user for 60 seconds and continues with the next loop on a sampler error. Set users
  and duration in the thread group before a real test. Recording, reviewing and saving never start a load test.
* Each request keeps the status code it got while recording and checks it (redirects are not followed, as
  recorded), so a broken login shows up as errors rather than as fast responses.

## API

* `POST /api/performance/recordings` {url, allowed_hosts, include_static, auto_steps, browser}
* `GET /api/performance/recordings/current`
* `POST /api/performance/recordings/{id}/step` {name}, `.../stop`, `.../preview`, `.../script`, `DELETE .../{id}`
* `POST /api/performance/plans/import-har` {har, allowed_hosts, include_static, name, kind, filename, scenario_id}
* `GET /api/performance/plans/{id}/secrets`, `PUT /api/performance/plans/{id}/secrets/{name}` {value}

## Tests

`tests/test_perf_recording_23324.py` records a login journey in a real (headless) browser against a local site with
a CSRF form field, a session cookie and a JSON bearer token, checks the correlations and the secret, runs the
recorded script in JMeter 5.6.3 (no errors; the secrets file is gone afterwards and the password is in no file of
the run), and checks HAR import, filtering, and the missing-secret pre-run check.
