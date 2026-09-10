/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        primary: '#1A3C2E',
        accent: '#2D6A4F',
        background: '#F7F5F0',
        surface: '#FFFFFF',
        border: '#E8E4DC',
        text: '#1A1916',
        muted: '#6B6760',
        hint: '#9E9A94',
        success: { DEFAULT: '#2D6A4F', bg: '#EAF4EE' },
        warning: { DEFAULT: '#8B5E00', bg: '#FFF8E6' },
        danger: { DEFAULT: '#9B2C2C', bg: '#FEF0F0' },
        info: { DEFAULT: '#1A4E8C', bg: '#EEF4FD' },
      },
      fontFamily: { sans: ['Inter', 'system-ui', 'sans-serif'] },
    },
  },
  plugins: [],
}
