/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{vue,ts}"],
  theme: {
    extend: {
      colors: {
        slatebg: "#0f172a",
        cardbg: "#1e293b",
      },
    },
  },
  plugins: [],
};
