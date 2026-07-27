/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        primary: '#4361ee',
        secondary: '#805dca',
        success: '#00ab55',
        danger: '#e7515a',
        warning: '#e2a03f',
        info: '#2196f3',
        dark: '#3b3f5c',
        darkLight: '#191e3a',
        darker: '#0e1726',
      },
      fontFamily: {
        sans: ['Nunito', 'sans-serif'],
      }
    },
  },
  plugins: [],
}
