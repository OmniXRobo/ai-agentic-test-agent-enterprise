// Stand-in for www.saucedemo.com used by the AI-Driven Automation tests (same element ids, data-test attributes,
// placeholders, page addresses and texts as the public demo shop; no network needed).
const PRODUCTS = [
  ["sauce-labs-backpack", "Sauce Labs Backpack", "29.99"],
  ["sauce-labs-bike-light", "Sauce Labs Bike Light", "9.99"],
  ["sauce-labs-bolt-t-shirt", "Sauce Labs Bolt T-Shirt", "15.99"],
];
const USERS = {standard_user: "secret_sauce"};
const cart = () => JSON.parse(localStorage.getItem("cart-contents") || "[]");
const setCart = (c) => localStorage.setItem("cart-contents", JSON.stringify(c));
function guard() {
  if (!localStorage.getItem("session-username")) {
    location.href = "index.html";
    return false;
  }
  return true;
}
function header(title) {
  const n = cart().length;
  return `<div class="primary_header" data-test="primary-header"><button id="react-burger-menu-btn">Open Menu</button>
    <div class="app_logo">Swag Labs</div><div id="shopping_cart_container" class="shopping_cart_container">
    <a class="shopping_cart_link" data-test="shopping-cart-link">${n ? `<span class="shopping_cart_badge"
    data-test="shopping-cart-badge">${n}</span>` : ""}</a></div></div>
    <div class="header_secondary_container" data-test="secondary-header"><span class="title" data-test="title">${title}</span></div>`;
}
function wireCart() {
  document.querySelector(".shopping_cart_link").addEventListener("click", () => { location.href = "cart.html"; });
}
function login() {
  document.getElementById("login-button").addEventListener("click", (e) => {
    e.preventDefault();
    const u = document.getElementById("user-name").value, p = document.getElementById("password").value;
    const err = document.querySelector(".error-message-container");
    if (!u) { err.innerHTML = '<h3 data-test="error">Epic sadface: Username is required</h3>'; return; }
    if (USERS[u] !== p) {
      err.innerHTML = '<h3 data-test="error">Epic sadface: Username and password do not match any user in this service</h3>';
      return;
    }
    localStorage.setItem("session-username", u);
    setTimeout(() => { location.href = "inventory.html"; }, 150);
  });
}
function inventory() {
  if (!guard()) return;
  const render = () => {
    const c = cart();
    document.getElementById("root").innerHTML = header("Products") +
      `<div class="inventory_list" data-test="inventory-list">` + PRODUCTS.map(([id, name, price], i) =>
        `<div class="inventory_item" data-test="inventory-item"><a href="#" id="item-${i}-title-link"
         data-test="item-${i}-title-link"><div class="inventory_item_name" data-test="inventory-item-name">${name}</div></a>
         <div class="inventory_item_price" data-test="inventory-item-price">$${price}</div>` +
        (c.includes(id) ? `<button class="btn btn_secondary" id="remove-${id}" data-test="remove-${id}" name="remove-${id}">Remove</button>`
                        : `<button class="btn btn_primary" id="add-to-cart-${id}" data-test="add-to-cart-${id}" name="add-to-cart-${id}">Add to cart</button>`)
        + `</div>`).join("") + `</div>`;
    wireCart();
    PRODUCTS.forEach(([id]) => {
      const add = document.getElementById(`add-to-cart-${id}`), rem = document.getElementById(`remove-${id}`);
      if (add) add.addEventListener("click", () => { setCart([...cart(), id]); render(); });
      if (rem) rem.addEventListener("click", () => { setCart(cart().filter((x) => x !== id)); render(); });
    });
  };
  render();
}
function cartPage() {
  if (!guard()) return;
  const items = cart().map((id) => PRODUCTS.find((p) => p[0] === id));
  document.getElementById("root").innerHTML = header("Your Cart") + `<div class="cart_list" data-test="cart-list">` +
    items.map(([id, name, price]) => `<div class="cart_item" data-test="inventory-item"><div class="cart_quantity"
      data-test="item-quantity">1</div><div class="inventory_item_name" data-test="inventory-item-name">${name}</div>
      <div class="inventory_item_price" data-test="inventory-item-price">$${price}</div></div>`).join("") + `</div>
    <button class="btn btn_secondary back" id="continue-shopping" data-test="continue-shopping">Continue Shopping</button>
    <button class="btn btn_action checkout_button" id="checkout" data-test="checkout" name="checkout">Checkout</button>`;
  wireCart();
  document.getElementById("continue-shopping").addEventListener("click", () => { location.href = "inventory.html"; });
  document.getElementById("checkout").addEventListener("click", () => { location.href = "checkout-step-one.html"; });
}
function stepOne() {
  if (!guard()) return;
  document.getElementById("root").innerHTML = header("Checkout: Your Information") + `<form class="checkout_info">
    <input class="input_error form_input" placeholder="First Name" type="text" data-test="firstName" id="first-name" name="firstName" value="">
    <input class="input_error form_input" placeholder="Last Name" type="text" data-test="lastName" id="last-name" name="lastName" value="">
    <input class="input_error form_input" placeholder="Zip/Postal Code" type="text" data-test="postalCode" id="postal-code" name="postalCode" value="">
    <div class="error-message-container"></div>
    <button class="btn btn_secondary back cart_cancel_link" id="cancel" data-test="cancel" name="cancel" type="button">Cancel</button>
    <input type="submit" class="submit-button btn btn_primary cart_button btn_action" data-test="continue" id="continue" name="continue" value="Continue">
    </form>`;
  wireCart();
  document.getElementById("cancel").addEventListener("click", () => { location.href = "cart.html"; });
  document.querySelector("form").addEventListener("submit", (e) => {
    e.preventDefault();
    for (const [id, label] of [["first-name", "First Name"], ["last-name", "Last Name"], ["postal-code", "Postal Code"]]) {
      if (!document.getElementById(id).value) {
        document.querySelector(".error-message-container").innerHTML = `<h3 data-test="error">Error: ${label} is required</h3>`;
        return;
      }
    }
    location.href = "checkout-step-two.html";
  });
}
function stepTwo() {
  if (!guard()) return;
  const items = cart().map((id) => PRODUCTS.find((p) => p[0] === id));
  const total = items.reduce((s, p) => s + Number(p[2]), 0).toFixed(2);
  document.getElementById("root").innerHTML = header("Checkout: Overview") + items.map(([id, name]) =>
    `<div class="cart_item" data-test="inventory-item"><div class="inventory_item_name" data-test="inventory-item-name">${name}</div></div>`).join("") +
    `<div class="summary_subtotal_label" data-test="subtotal-label">Item total: $${total}</div>
    <button class="btn btn_secondary back cart_cancel_link" id="cancel" data-test="cancel" name="cancel">Cancel</button>
    <button class="btn btn_action cart_button" id="finish" data-test="finish" name="finish">Finish</button>`;
  wireCart();
  document.getElementById("cancel").addEventListener("click", () => { location.href = "inventory.html"; });
  document.getElementById("finish").addEventListener("click", () => { setCart([]); location.href = "checkout-complete.html"; });
}
function complete() {
  if (!guard()) return;
  document.getElementById("root").innerHTML = header("Checkout: Complete!") + `<div class="checkout_complete_container"
    data-test="checkout-complete-container"><h2 class="complete-header" data-test="complete-header">Thank you for your order!</h2>
    <div class="complete-text" data-test="complete-text">Your order has been dispatched, and will arrive just as fast as the pony can get there!</div>
    <button class="btn btn_primary btn_small" id="back-to-products" data-test="back-to-products" name="back-to-products">Back Home</button></div>`;
  wireCart();
  document.getElementById("back-to-products").addEventListener("click", () => { location.href = "inventory.html"; });
}
