/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#f8fbff",
          900: "#eef6ff",
          850: "#e3f0ff",
          800: "#cfe3fb",
          700: "#9fc5ef",
        },
        accent: {
          DEFAULT: "#2563eb",
          soft: "#60a5fa",
        },
      },
    },
  },
  plugins: [],
};
