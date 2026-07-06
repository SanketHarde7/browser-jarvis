/**
 * 🔧 Quick Skill Chips — Left-side vertical panel
 * Moved from bottom-center to left side to stop overlapping mic button.
 */
import { motion } from 'framer-motion'

const SKILLS = [
  { label: '🌤️ Weather',  desc: 'Check Pune weather' },
  { label: '⏱️ Timer',    desc: 'Set 60 second timer' },
  { label: '📝 Note',     desc: 'Save a quick note' },
  { label: '🔍 Search',   desc: 'Search latest AI news' },
  { label: '📓 Notepad',  desc: 'Open Notepad' },
  { label: '📸 Screenshot', desc: 'Take a screenshot' },
  { label: '🎥 Record Screen', desc: 'Toggle screen recording' },
  { label: '📋 Clipboard', desc: 'Clipboard content batao' },
  { label: '🔒 Lock PC',  desc: 'System lock karo' },
  { label: '🔊 Volume',   desc: 'Volume up karo' },
  { label: '📅 Calendar', desc: 'Aaj ka schedule batao' },
]

export default function SkillChips({ onSkillSelect, disabled = false }) {
  return (
    <motion.div
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: 0.5, duration: 0.4 }}
      style={{
        position: 'fixed',
        left: '1.25rem',
        top: '50%',
        transform: 'translateY(-50%)',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.4rem',
        zIndex: 20,
        maxHeight: '80vh',
        overflowY: 'auto',
        paddingRight: '4px',  // room for scrollbar
      }}
    >
      {/* Section label */}
      <div
        style={{
          fontFamily: "'Orbitron', monospace",
          fontSize: '0.55rem',
          letterSpacing: '2px',
          color: 'rgba(0, 212, 255, 0.4)',
          marginBottom: '0.25rem',
          paddingLeft: '2px',
        }}
      >
        QUICK ACTIONS
      </div>

      {SKILLS.map((skill, i) => (
        <motion.button
          key={skill.label}
          initial={{ opacity: 0, x: -12 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.55 + i * 0.04 }}
          whileHover={{ x: 4, scale: 1.02 }}
          whileTap={{ scale: 0.96 }}
          onClick={() => {
            if (!disabled) onSkillSelect?.(skill.desc)
          }}
          disabled={disabled}
          title={skill.desc}
          style={{
            background: 'rgba(5, 12, 24, 0.75)',
            backdropFilter: 'blur(12px)',
            WebkitBackdropFilter: 'blur(12px)',
            border: '1px solid rgba(0, 212, 255, 0.12)',
            borderLeft: '2px solid rgba(0, 212, 255, 0.35)',
            borderRadius: '0 10px 10px 0',
            padding: '0.35rem 0.75rem',
            color: disabled ? 'rgba(0,212,255,0.25)' : '#00d4ff',
            fontSize: '0.75rem',
            fontFamily: "'Inter', sans-serif",
            fontWeight: 500,
            cursor: disabled ? 'not-allowed' : 'pointer',
            transition: 'all 0.18s ease',
            boxShadow: '2px 2px 8px rgba(0,0,0,0.25)',
            opacity: disabled ? 0.35 : 1,
            textAlign: 'left',
            whiteSpace: 'nowrap',
            minWidth: '120px',
          }}
          onMouseEnter={(e) => {
            if (!disabled) {
              e.currentTarget.style.borderLeftColor = 'rgba(0, 212, 255, 0.8)'
              e.currentTarget.style.background = 'rgba(0, 212, 255, 0.08)'
              e.currentTarget.style.boxShadow = '4px 4px 16px rgba(0, 212, 255, 0.12)'
            }
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.borderLeftColor = 'rgba(0, 212, 255, 0.35)'
            e.currentTarget.style.background = 'rgba(5, 12, 24, 0.75)'
            e.currentTarget.style.boxShadow = '2px 2px 8px rgba(0,0,0,0.25)'
          }}
        >
          {skill.label}
        </motion.button>
      ))}
    </motion.div>
  )
}
