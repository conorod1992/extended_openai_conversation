import "./management-state-safety.js";

if (typeof customElements !== "undefined") {
  await import("./management-rendering-performance.js");
  await import("./management-loading-performance.js");
  await import("./management-route-performance.js");
}
