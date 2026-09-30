(function () {
  const form = document.getElementById("search-form");
  if (!form) return;

  const STORAGE_KEY = "library-criteria";
  const FIELDS = [
    "prefer_verified",
    "w_verified",
    "age",
    "w_age",
    "country",
    "w_country",
    "prefer_ai",
    "w_ai",
    "w_popularity",
  ];

  function ageLabel(value) {
    const minDays = 3;
    const maxDays = 30 * 365.25;
    const t = Math.min(100, Math.max(0, Number(value) || 0)) / 100;
    const days = minDays * Math.pow(maxDays / minDays, t);
    if (days < 14) return "a few days";
    if (days < 45) return "about a month";
    if (days < 300) return "several months";
    const years = days / 365.25;
    if (years < 1.4) return "about a year";
    if (years >= 25) return "30+ years";
    return "about " + Math.round(years) + " years";
  }

  function read() {
    const data = {};
    FIELDS.forEach(function (name) {
      const field = form.elements[name];
      if (field) data[name] = field.value;
    });
    return data;
  }

  function write(data) {
    FIELDS.forEach(function (name) {
      if (data[name] === undefined || !form.elements[name]) return;
      form.elements[name].value = data[name];
    });
  }

  function refresh() {
    const label = document.getElementById("age-label");
    const cursor = form.elements.age;
    if (label && cursor) label.textContent = ageLabel(cursor.value);
    form.querySelectorAll(".weight-line").forEach(function (line) {
      const slider = line.querySelector("input[type=range]");
      const output = line.querySelector("output");
      if (slider && output) output.textContent = slider.value;
    });
  }

  function save() {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(read()));
  }

  function isDefault(data) {
    const weights = ["w_verified", "w_age", "w_country", "w_ai", "w_popularity", "age"];
    return (
      (data.prefer_verified || "any") === "any" &&
      (data.prefer_ai || "any") === "any" &&
      !data.country &&
      weights.every(function (name) {
        return String(data[name] || "0") === "0";
      })
    );
  }

  const params = new URLSearchParams(window.location.search);
  const echoed = FIELDS.some(function (name) {
    return params.has(name);
  });
  if (!echoed) {
    try {
      const stored = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "null");
      if (stored && !isDefault(stored)) {
        write(stored);
        refresh();
        form.requestSubmit();
        return;
      }
    } catch (err) {
      sessionStorage.removeItem(STORAGE_KEY);
    }
  }
  refresh();
  save();

  form.addEventListener("input", function () {
    refresh();
    save();
  });
  form.addEventListener("submit", save);

  const reset = document.getElementById("reset-criteria");
  if (reset) {
    reset.addEventListener("click", function () {
      write({
        prefer_verified: "any",
        w_verified: "0",
        age: "0",
        w_age: "0",
        country: "",
        w_country: "0",
        prefer_ai: "any",
        w_ai: "0",
        w_popularity: "0",
      });
      refresh();
      save();
      form.requestSubmit();
    });
  }
})();
