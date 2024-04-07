document.addEventListener(
  "DOMContentLoaded",
  (event) => {
    particlesJS.load("particles-js", "/static/assets/json/particles.json", function () {
      console.log("particles.js loaded - callback");
    });
  },
  false
);
