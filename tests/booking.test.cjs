const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../static/booking.js"), "utf8");
const held = { seat: "A1", hold_token: "test-token", expires_at: "2026-12-04T19:00:00+05:30" };
const ticket = { seat: "A1", booking_id: "saved-ticket" };

function page(fetch, saved = null) {
  function element(dataset = {}) {
    return {
      dataset, hidden: false, disabled: true, textContent: "", listeners: {},
      addEventListener(name, callback) { this.listeners[name] = callback; },
    };
  }
  const elements = {};
  for (const id of ["reservation", "booking-message", "confirm-booking", "another-seat",
                    "storage-warning", "reservation-title", "hold-summary", "ticket"]) {
    elements["#" + id] = element();
  }
  elements["#reservation"].dataset = { holdUrl: "/events/1/holds", bookingUrl: "/events/1/bookings" };
  const seats = ["A1", "A2"].map(seat => element({ seat, status: "Available" }));
  let storage = saved ? JSON.stringify(saved) : null;
  let reloaded = false;
  vm.runInNewContext(source, {
    document: {
      querySelector: selector => elements[selector],
      querySelectorAll: () => seats,
    },
    sessionStorage: {
      getItem: () => storage,
      setItem: (_, value) => { storage = value; },
      removeItem: () => { storage = null; },
    },
    location: { reload() { reloaded = true; } },
    fetch, AbortSignal, Date,
  });
  return {
    elements, seats,
    click: target => target.listeners.click(),
    saved: () => JSON.parse(storage),
    reloaded: () => reloaded,
  };
}

function response(status, data) {
  return { ok: status >= 200 && status < 300, status, json: async () => structuredClone(data) };
}

test("holds and confirms a seat, saves the ticket, and can start again", async () => {
  const requests = [];
  const ui = page(async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) });
    return url.endsWith("/holds") ? response(201, held) : response(201, ticket);
  });
  assert.equal(ui.seats[0].disabled, false);
  await ui.click(ui.seats[0]);
  assert.equal(ui.elements["#reservation"].hidden, false);
  assert.equal(ui.seats[1].disabled, true);
  assert.equal(ui.saved().hold_token, held.hold_token);
  await ui.click(ui.elements["#confirm-booking"]);
  assert.equal(ui.elements["#reservation-title"].textContent, "Booking confirmed");
  assert.equal(ui.elements["#ticket"].textContent, "Ticket saved-ticket");
  assert.equal(requests[1].body.hold_token, held.hold_token);
  ui.click(ui.elements["#another-seat"]);
  assert.equal(ui.saved(), null);
  assert.equal(ui.reloaded(), true);
});

test("refresh restores the hold and the confirmed receipt", () => {
  const pending = page(async () => { throw new Error("No request expected"); }, held);
  assert.equal(pending.elements["#reservation"].hidden, false);
  assert.equal(pending.elements["#confirm-booking"].hidden, false);
  const confirmed = page(async () => { throw new Error("No request expected"); }, { ...held, booking: ticket });
  assert.equal(confirmed.elements["#ticket"].textContent, "Ticket saved-ticket");
  assert.equal(confirmed.elements["#confirm-booking"].hidden, true);
});

test("lost confirmation response preserves the token for the same retry", async () => {
  let attempts = 0;
  const requests = [];
  const ui = page(async (_, options) => {
    requests.push(JSON.parse(options.body));
    if (++attempts === 1) throw new Error("Response lost");
    return response(200, ticket);
  }, held);
  await ui.click(ui.elements["#confirm-booking"]);
  assert.equal(ui.saved().hold_token, held.hold_token);
  assert.equal(ui.elements["#confirm-booking"].disabled, false);
  await ui.click(ui.elements["#confirm-booking"]);
  assert.deepEqual(requests[0], requests[1]);
  assert.equal(ui.elements["#ticket"].textContent, "Ticket saved-ticket");
});

test("a rejected hold confirmation clears stale state and shows the error", async () => {
  const ui = page(async () => response(409, { error: "Hold is expired." }), held);
  await ui.click(ui.elements["#confirm-booking"]);
  assert.equal(ui.saved(), null);
  assert.equal(ui.elements["#reservation"].hidden, true);
  assert.match(ui.elements["#booking-message"].textContent, /Hold is expired/);
});
