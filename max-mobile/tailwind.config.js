/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: {
          primary: '#05070B',
          secondary: '#0A0F18',
          tertiary: '#111827',
        },
        glow: {
          primary: '#52C8FF',
          secondary: '#8B5CF6',
          accent: '#D946EF',
        },
        text: {
          primary: '#F8FAFC',
          secondary: 'rgba(255, 255, 255, 0.72)',
          muted: 'rgba(255, 255, 255, 0.42)',
        },
        divider: 'rgba(255, 255, 255, 0.08)',
      },
      boxShadow: {
        'glass-small': '0 8px 24px rgba(30, 144, 255, 0.08)',
        'glass-medium': '0 20px 50px rgba(82, 200, 255, 0.12)',
        'glass-large': '0 0 120px rgba(82, 200, 255, 0.18)',
        'glass-ambient': 'inset 0 0 30px rgba(255, 255, 255, 0.03)',
      },
      fontFamily: {
        sans: ['"SF Pro Display"', 'Inter', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
