/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#070b10",
        panel: "#10161d",
        line: "#243041",
        mint: "#3dd68c",
        amber: "#e2b340",
        rose: "#ef6b6b",
        mist: "#8ea0b5",
      },
      fontFamily: {
        sans: ["Rubik", "Arial", "sans-serif"],
      },
    },
  },
  plugins: [],
};
