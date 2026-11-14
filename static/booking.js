const reservation = document.querySelector("#reservation");
const seats = [...document.querySelectorAll(".seat")];
const message = document.querySelector("#booking-message");
const confirmButton = document.querySelector("#confirm-booking");
const anotherButton = document.querySelector("#another-seat");
const storageKey = "lastseat:" + reservation.dataset.holdUrl;
let hold = null;
let busy = false;

try {
  const saved = JSON.parse(sessionStorage.getItem(storageKey));
  if (saved && typeof saved.seat === "string" && typeof saved.hold_token === "string") {
    hold = saved;
  }
} catch {
  document.querySelector("#storage-warning").hidden = false;
}

function saveHold() {
  try {
    if (hold) {
      sessionStorage.setItem(storageKey, JSON.stringify(hold));
    } else {
      sessionStorage.removeItem(storageKey);
    }
  } catch {
    document.querySelector("#storage-warning").hidden = false;
  }
}

function render() {
  for (const button of seats) {
    button.disabled = busy || Boolean(hold) || button.dataset.status !== "Available";
  }
  reservation.hidden = !hold;
  confirmButton.disabled = busy;
  if (!hold) return;

  const booked = Boolean(hold.booking);
  document.querySelector("#reservation-title").textContent = booked ? "Booking confirmed" : "Your hold";
  document.querySelector("#hold-summary").textContent = booked
    ? "Seat " + hold.seat
    : "Seat " + hold.seat + " held until " + new Date(hold.expires_at).toLocaleTimeString();
  document.querySelector("#ticket").textContent = booked ? "Ticket " + hold.booking.booking_id : "";
  confirmButton.hidden = booked;
  anotherButton.hidden = !booked;
}

async function post(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(10000),
  });
  return { response, data: await response.json() };
}

for (const button of seats) {
  button.addEventListener("click", async () => {
    if (busy || hold) return;
    busy = true;
    message.textContent = "Holding your seat…";
    render();
    try {
      const { response, data } = await post(reservation.dataset.holdUrl, { seat: button.dataset.seat });
      if (response.ok) {
        hold = data;
        saveHold();
        message.textContent = "Your seat is held. Confirm before the hold expires.";
      } else {
        message.textContent = data.error + " Refresh availability before choosing again.";
      }
    } catch {
      message.textContent = "Could not retrieve the hold response. Refresh availability; any unconfirmed hold expires in two minutes.";
    } finally {
      busy = false;
      render();
    }
  });
}

confirmButton.addEventListener("click", async () => {
  if (busy || !hold) return;
  busy = true;
  message.textContent = "Confirming your booking…";
  render();
  try {
    const { response, data } = await post(reservation.dataset.bookingUrl, {
      seat: hold.seat, hold_token: hold.hold_token,
    });
    if (response.ok) {
      hold.booking = data;
      saveHold();
      message.textContent = "Save your ticket number.";
    } else if (response.status === 409 || response.status === 400) {
      hold = null;
      saveHold();
      message.textContent = data.error + " Refresh availability to choose a seat.";
    } else {
      message.textContent = "Confirmation could not be retrieved. Retry confirmation to recover the same ticket.";
      confirmButton.textContent = "Retry confirmation";
    }
  } catch {
    message.textContent = "Confirmation could not be retrieved. Retry confirmation to recover the same ticket.";
    confirmButton.textContent = "Retry confirmation";
  } finally {
    busy = false;
    render();
  }
});

anotherButton.addEventListener("click", () => {
  hold = null;
  saveHold();
  location.reload();
});

render();
