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
      // Same custom scale as PoultryPro-CBF's frontend (this hub's design
      // system is ported from it) — a fixed line-height paired with each
      // size rather than relying on Tailwind's defaults.
      fontSize: {
        xs: ['11px', '16px'],
        sm: ['12px', '18px'],
        base: ['14px', '20px'],
        md: ['15px', '22px'],
        lg: ['17px', '24px'],
        xl: ['20px', '28px'],
        '2xl': ['24px', '32px'],
      },
      spacing: {
        '1': '4px', '2': '8px', '3': '12px', '4': '16px', '5': '20px',
        '6': '24px', '8': '32px', '10': '40px', '12': '48px',
      },
      borderRadius: { sm: '6px', DEFAULT: '8px', lg: '12px', xl: '16px' },
      boxShadow: {
        subtle: '0 1px 3px rgba(0,0,0,.06), 0 1px 2px rgba(0,0,0,.04)',
        card: '0 2px 8px rgba(0,0,0,.08)',
      },
    },
  },
  plugins: [],
}
