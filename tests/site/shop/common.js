// Test shop (SauceDemo-like single-page navigation without real links). Test fixture only.
const shop = {
  user() { return localStorage.getItem("shop_user"); },
  guard() { if (!shop.user()) location.replace("index.html"); },
  go(page) { location.href = page; },
  cart() { return JSON.parse(localStorage.getItem("shop_cart") || "[]"); },
  setCart(c) { localStorage.setItem("shop_cart", JSON.stringify(c)); shop.badge(); },
  badge() { const b = document.querySelector(".shopping_cart_badge"); if (b) { const n = shop.cart().length; b.textContent = n || ""; } },
  header() {
    document.body.insertAdjacentHTML("afterbegin", `
      <div class="header">
        <button id="react-burger-menu-btn" aria-label="Open Menu">Open Menu</button>
        <div class="app_logo">Test Shop</div>
        <a class="shopping_cart_link" data-test="shopping-cart-link"><span class="shopping_cart_badge"></span>Cart</a>
      </div>
      <nav class="bm-menu" hidden aria-label="Main menu">
        <a id="inventory_sidebar_link" href="#">All Items</a>
        <a id="about_sidebar_link" href="https://example.invalid/about">About</a>
        <a id="logout_sidebar_link" href="#">Logout</a>
        <a id="reset_sidebar_link" href="#">Reset App State</a>
      </nav>`);
    document.getElementById("react-burger-menu-btn").onclick = () => { document.querySelector(".bm-menu").hidden = false; };
    document.querySelector(".shopping_cart_link").onclick = () => shop.go("cart.html");
    document.getElementById("inventory_sidebar_link").onclick = (e) => { e.preventDefault(); shop.go("inventory.html"); };
    document.getElementById("logout_sidebar_link").onclick = (e) => { e.preventDefault(); localStorage.clear(); shop.go("index.html"); };
    document.getElementById("reset_sidebar_link").onclick = (e) => { e.preventDefault(); shop.setCart([]); };
    shop.badge();
  },
};
