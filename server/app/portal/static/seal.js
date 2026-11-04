// Partner keys in the browser (ADR-053). The private keys are made here, locked with the
// partner's passphrase before they leave, and unlocked here; the server only ever holds the
// locked bundle. Unlocked keys are kept in this browser's IndexedDB as non-extractable keys
// for KEEP_MS, then forgotten. server/app/services/keys.py is the other half; the formats match.
(function () {
  "use strict";
  var subtle = (globalThis.crypto || {}).subtle;
  var ROUNDS = 600000;
  var KEEP_MS = 12 * 3600 * 1000;
  var CHECKPOINT_MS = 3600 * 1000;
  var INFO = "watchman seal v1";
  var enc = new TextEncoder(), dec = new TextDecoder();

  function b64(buf) {
    var bytes = new Uint8Array(buf), s = "";
    for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s);
  }
  function unb64(text) {
    var s = atob(text), out = new Uint8Array(s.length);
    for (var i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
  }
  function canonical(v) {
    if (Array.isArray(v)) return "[" + v.map(canonical).join(",") + "]";
    if (v && typeof v === "object") {
      return "{" + Object.keys(v).sort().map(function (k) { return JSON.stringify(k) + ":" + canonical(v[k]); }).join(",") + "}";
    }
    return JSON.stringify(v);
  }

  // A small IndexedDB store for unlocked keys; a plain map where there is none (the tests run in Node).
  var memory = {};
  function db() {
    if (typeof indexedDB === "undefined") return Promise.reject(new Error("no IndexedDB"));
    return new Promise(function (resolve, reject) {
      var req = indexedDB.open("watchman-keys", 1);
      req.onupgradeneeded = function () { req.result.createObjectStore("keys"); };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  }
  function idb(mode, fn) {
    return db().then(function (d) {
      return new Promise(function (resolve, reject) {
        var tx = d.transaction("keys", mode), store = tx.objectStore("keys"), out = fn(store);
        tx.oncomplete = function () { resolve(out && out.result !== undefined ? out.result : out); };
        tx.onerror = function () { reject(tx.error); };
      });
    });
  }

  function passKey(passphrase, salt, rounds) {
    return subtle.importKey("raw", enc.encode(passphrase), "PBKDF2", false, ["deriveKey"]).then(function (base) {
      return subtle.deriveKey({name: "PBKDF2", salt: salt, iterations: rounds, hash: "SHA-256"}, base,
                              {name: "AES-GCM", length: 256}, false, ["encrypt", "decrypt"]);
    });
  }

  function remember(partnerId, signKey, encKey, fingerprint) {
    var value = {sign: signKey, enc: encKey, fingerprint: fingerprint, at: Date.now()};
    if (typeof indexedDB === "undefined") { memory[partnerId] = value; return Promise.resolve(); }
    return idb("readwrite", function (s) { return s.put(value, partnerId); });
  }

  function importPrivate(bundle) {
    return Promise.all([
      subtle.importKey("pkcs8", unb64(bundle.sign), {name: "ECDSA", namedCurve: "P-256"}, false, ["sign"]),
      subtle.importKey("pkcs8", unb64(bundle.enc), {name: "ECDH", namedCurve: "P-256"}, false, ["deriveBits"]),
    ]);
  }

  // Make both key pairs, lock the private halves with the passphrase and send the result.
  function setup(partnerId, passphrase, post) {
    var sign, ecdh;
    post = post || postJson;
    return Promise.all([
      subtle.generateKey({name: "ECDSA", namedCurve: "P-256"}, true, ["sign", "verify"]),
      subtle.generateKey({name: "ECDH", namedCurve: "P-256"}, true, ["deriveBits"]),
    ]).then(function (pairs) {
      sign = pairs[0]; ecdh = pairs[1];
      return Promise.all([
        subtle.exportKey("spki", sign.publicKey), subtle.exportKey("raw", ecdh.publicKey),
        subtle.exportKey("pkcs8", sign.privateKey), subtle.exportKey("pkcs8", ecdh.privateKey),
      ]);
    }).then(function (x) {
      var salt = crypto.getRandomValues(new Uint8Array(16)), iv = crypto.getRandomValues(new Uint8Array(12));
      var bundle = enc.encode(JSON.stringify({sign: b64(x[2]), enc: b64(x[3])}));
      return passKey(passphrase, salt, ROUNDS).then(function (k) {
        return subtle.encrypt({name: "AES-GCM", iv: iv}, k, bundle);
      }).then(function (data) {
        return post("/keys", {partner_id: partnerId, sign_pub: b64(x[0]), enc_pub: b64(x[1]),
                              wrapped: {salt: b64(salt), iterations: ROUNDS, iv: b64(iv), data: b64(data)}});
      }).then(function (r) {
        return importPrivate({sign: b64(x[2]), enc: b64(x[3])}).then(function (keys) {
          return remember(partnerId, keys[0], keys[1], r.fingerprint).then(function () { return r.fingerprint; });
        });
      });
    });
  }

  // Unlock every key this login holds. A wrong passphrase rejects.
  function unlock(passphrase, mine) {
    var got = mine ? Promise.resolve(mine) : fetch("/keys/mine", {credentials: "same-origin"}).then(function (r) { return r.json(); });
    return got.then(function (m) {
      return Promise.all(m.keys.map(function (k) {
        return passKey(passphrase, unb64(k.wrapped.salt), k.wrapped.iterations).then(function (pk) {
          return subtle.decrypt({name: "AES-GCM", iv: unb64(k.wrapped.iv)}, pk, unb64(k.wrapped.data));
        }).then(function (plain) {
          return importPrivate(JSON.parse(dec.decode(plain)));
        }).then(function (keys) {
          return remember(k.partner_id, keys[0], keys[1], k.fingerprint);
        });
      }));
    }).then(function (done) { return done.length; });
  }

  function lock() {
    memory = {};
    if (typeof indexedDB === "undefined") return Promise.resolve();
    return idb("readwrite", function (s) { return s.clear(); });
  }

  function unlocked() {
    if (typeof indexedDB === "undefined") return Promise.resolve(memory);
    return db().then(function (d) {
      return new Promise(function (resolve, reject) {
        var tx = d.transaction("keys", "readonly"), store = tx.objectStore("keys");
        var ids = store.getAllKeys(), values = store.getAll();
        tx.oncomplete = function () {
          var out = {};
          ids.result.forEach(function (id, i) {
            var v = values.result[i];
            if (v && Date.now() - v.at < KEEP_MS) out[id] = v;
          });
          resolve(out);
        };
        tx.onerror = function () { reject(tx.error); };
      });
    }).catch(function () { return {}; });
  }

  function postJson(url, body) {
    return fetch(url, {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"},
                       body: JSON.stringify(body)}).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error((j.error && j.error.message) || r.status); return j; });
    });
  }

  // Sign {"v":1, action, params, nonce, partner_id}; the nonce comes from the server, single use.
  function sign(partnerId, action, params, keys, challenge) {
    var key = keys && keys[partnerId];
    if (!key) return Promise.reject(new Error("unlock your key first"));
    challenge = challenge || function () { return postJson("/keys/challenge", {partner_id: partnerId}); };
    return challenge().then(function (c) {
      var payload = canonical({v: 1, action: action, params: params, nonce: c.nonce, partner_id: partnerId});
      return subtle.sign({name: "ECDSA", hash: "SHA-256"}, key.sign, enc.encode(payload)).then(function (sig) {
        return {payload: payload, sig: b64(sig), partner_id: partnerId};
      });
    });
  }

  function hkdfKey(shared) {
    return subtle.importKey("raw", shared, "HKDF", false, ["deriveKey"]).then(function (k) {
      return subtle.deriveKey({name: "HKDF", hash: "SHA-256", salt: new Uint8Array(0), info: enc.encode(INFO)}, k,
                              {name: "AES-GCM", length: 256}, false, ["decrypt"]);
    });
  }

  // Open an envelope from keys.seal with this partner's key.
  function open(envelope, aad, partnerId, encKey) {
    var env = typeof envelope === "string" ? JSON.parse(envelope) : envelope;
    var k = env.keys[partnerId];
    if (!k) return Promise.reject(new Error("not sealed for this partner"));
    var ad = enc.encode(aad);
    return subtle.importKey("raw", unb64(k.epk), {name: "ECDH", namedCurve: "P-256"}, false, []).then(function (epk) {
      return subtle.deriveBits({name: "ECDH", public: epk}, encKey, 256);
    }).then(hkdfKey).then(function (kek) {
      return subtle.decrypt({name: "AES-GCM", iv: unb64(k.iv), additionalData: ad}, kek, unb64(k.wk));
    }).then(function (cek) {
      return subtle.importKey("raw", cek, {name: "AES-GCM"}, false, ["decrypt"]);
    }).then(function (cek) {
      return subtle.decrypt({name: "AES-GCM", iv: unb64(env.iv), additionalData: ad}, cek, unb64(env.ct));
    });
  }

  function openAny(envelope, aad, keys) {
    var env = typeof envelope === "string" ? JSON.parse(envelope) : envelope;
    var id = Object.keys(env.keys).filter(function (p) { return keys[p]; })[0];
    if (!id) return Promise.reject(new Error("locked"));
    return open(env, aad, id, keys[id].enc);
  }

  // Page behaviour: sealed text, signed forms, checkpoints.
  function onPage() {
    unlocked().then(function (keys) {
      var have = Object.keys(keys).length > 0;
      document.querySelectorAll("[data-sealed]").forEach(function (el) {
        if (!have) { el.textContent = "sealed for partners"; return; }
        openAny(el.getAttribute("data-sealed"), el.getAttribute("data-aad"), keys).then(function (plain) {
          var v = JSON.parse(dec.decode(plain));
          el.textContent = [v.url, v.title].filter(Boolean).join(" · ") || "nothing recorded";
        }).catch(function () { el.textContent = "sealed for partners"; });
      });
      document.querySelectorAll("form[data-sign-action]").forEach(function (form) {
        form.addEventListener("submit", function (ev) {
          if (form.dataset.signed) return;
          ev.preventDefault();
          var params = JSON.parse(form.getAttribute("data-sign-params") || "{}");
          var submitter = ev.submitter;
          (form.getAttribute("data-sign-fields") || "").split(",").filter(Boolean).forEach(function (f) {
            var kind = f.split(":"), name = kind[0], el = form.elements[name];
            var value = submitter && submitter.name === name ? submitter.value : (el ? el.value : null);
            params[name] = kind[1] === "int" ? parseInt(value, 10) : value;
          });
          var partner = form.getAttribute("data-partner");
          sign(partner, form.getAttribute("data-sign-action"), params, keys).then(function (a) {
            [["approval_payload", a.payload], ["approval_sig", a.sig], ["approval_partner", a.partner_id]].forEach(function (p) {
              var input = document.createElement("input");
              input.type = "hidden"; input.name = p[0]; input.value = p[1]; form.appendChild(input);
            });
            if (submitter && submitter.name) {
              var keep = document.createElement("input");
              keep.type = "hidden"; keep.name = submitter.name; keep.value = submitter.value; form.appendChild(keep);
            }
            form.dataset.signed = "1";
            form.submit();
          }).catch(function (err) {
            var note = form.querySelector(".sign-note") || form.appendChild(document.createElement("p"));
            note.className = "sign-note notice bad";
            note.innerHTML = "";
            note.appendChild(document.createTextNode(err.message + ". "));
            var a = document.createElement("a");
            a.href = "/keys"; a.textContent = "Unlock your key";
            note.appendChild(a);
          });
        });
      });
      if (have) checkpoint(keys);
    });
  }

  // Sign the record's head now and then, and check the head signed last time is still there.
  function checkpoint(keys) {
    var last = null;
    try { last = JSON.parse(localStorage.getItem("watchman-checkpoint") || "null"); } catch (e) { last = null; }
    var check = last ? fetch("/record/entry/" + last.seq, {credentials: "same-origin"}).then(function (r) {
      return r.ok ? r.json() : {hash: "missing"};
    }).then(function (e) {
      if (e.hash !== last.hash) {
        var bar = document.createElement("p");
        bar.className = "notice bad";
        bar.textContent = "The record changed after you checked it: entry " + last.seq + " is not what your browser signed. " +
          "Someone altered this server's history.";
        var main = document.getElementById("main") || document.body;
        main.insertBefore(bar, main.firstChild);
        postJson("/record/mismatch", {seq: last.seq, hash: last.hash, now: e.hash}).catch(function () {});
      }
    }) : Promise.resolve();
    check.then(function () {
      if (last && Date.now() - last.at < CHECKPOINT_MS) return;
      var partner = Object.keys(keys)[0];
      return fetch("/record/head", {credentials: "same-origin"}).then(function (r) { return r.json(); }).then(function (head) {
        if (!head.seq) return;
        return sign(partner, "checkpoint", {seq: head.seq, hash: head.hash}, keys).then(function (a) {
          return postJson("/record/checkpoint", a);
        }).then(function () {
          localStorage.setItem("watchman-checkpoint", JSON.stringify({seq: head.seq, hash: head.hash, at: Date.now()}));
        });
      });
    }).catch(function () {});
  }

  globalThis.WatchmanSeal = {setup: setup, unlock: unlock, lock: lock, unlocked: unlocked, sign: sign,
                             open: open, openAny: openAny, canonical: canonical, b64: b64, unb64: unb64};
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", onPage); else onPage();
  }
})();
