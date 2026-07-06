import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';

export default function TypingInput({ onSend, disabled }) {
  const [text, setText] = useState('');
  const [isFocused, setIsFocused] = useState(false);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (text.trim() && !disabled) {
      onSend(text.trim());
      setText('');
    }
  };

  return (
    <motion.div
      initial={{ y: 20, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      className="glass" // Use the new PRD glass utility
      style={{
        width: isFocused ? '300px' : '260px',
        borderRadius: '24px',
        padding: '8px 16px',
        display: 'flex',
        alignItems: 'center',
        gap: '12px',
        transition: 'width 0.3s cubic-bezier(0.16, 1, 0.3, 1)',
        marginTop: '1rem',
      }}
    >
      <form onSubmit={handleSubmit} style={{ display: 'flex', width: '100%', alignItems: 'center' }}>
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          disabled={disabled}
          placeholder="Message MAX..."
          style={{
            flex: 1,
            background: 'transparent',
            border: 'none',
            color: '#F8FAFC', // PRD Primary text
            fontSize: '0.95rem',
            outline: 'none',
            fontFamily: '"SF Pro Display", Inter, sans-serif'
          }}
        />
        <AnimatePresence>
          {text.trim() && (
            <motion.button
              initial={{ scale: 0, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0, opacity: 0 }}
              type="submit"
              disabled={disabled}
              style={{
                background: 'rgba(82, 200, 255, 0.2)',
                color: '#52C8FF',
                border: 'none',
                borderRadius: '50%',
                width: '32px',
                height: '32px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
              }}
            >
              ↑
            </motion.button>
          )}
        </AnimatePresence>
      </form>
    </motion.div>
  );
}
