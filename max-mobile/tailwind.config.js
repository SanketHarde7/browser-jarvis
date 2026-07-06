/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: '#050505',
        primary: '#ffffff',
        accent: 'rgba(255, 255, 255, 0.1)',
      }
    },
  },
  plugins: [],
}
