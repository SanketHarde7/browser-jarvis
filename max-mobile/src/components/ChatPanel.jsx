/**
 * 💬 Chat Panel — Premium glass message bubbles
 * Uses the glass depth system from index.css for visual hierarchy
 */
import { useEffect, useRef } from 'react'
import { AnimatePresence, motion } from 'framer-motion'

const msgVariants = {
  initial: { opacity: 0, y: 12, scale: 0.97 },
  animate: { opacity: 1, y: 0, scale: 1 },
  exit: { opacity: 0, y: -8, scale: 0.97 },
}

const springTransition = { duration: 0.28, ease: [0.16, 1, 0.3, 1] }

export default function ChatPanel({ messages = [], isProcessing = false, userName = 'User' }) {
  const scrollRef = useRef(null)

  useEffect(() => {
    if (!scrollRef.current) return
    scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, isProcessing])

  const hasMessages = messages.length > 0 || isProcessing

  // Determine time-appropriate greeting
  const getGreeting = () => {
    const hour = new Date().getHours()
    if (hour < 12) return 'Good Morning'
    if (hour < 17) return 'Good Afternoon'
    return 'Good Evening'
  }

  return (
    <section className="conversation-layer" aria-label="Conversation">
      <div ref={scrollRef} className="conversation-scroll">
        <AnimatePresence mode="popLayout">
          {/* Empty state greeting */}
          {!hasMessages && (
            <motion.div
              key="greeting"
              className="greeting-card glass-2"
              variants={msgVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
            >
              <div>
                <h2>{getGreeting()}, {userName}</h2>
                <p>How can I help you today?</p>
              </div>
              <span className="greeting-arrow" aria-hidden="true">›</span>
            </motion.div>
          )}

          {/* Messages */}
          {messages.map((msg, idx) => {
            const isUser = msg.role === 'user'
            return (
              <motion.article
                key={`${msg.role}-${idx}-${msg.content?.slice(0, 12)}`}
                className={`msg-row ${isUser ? 'msg-row-user' : 'msg-row-ai'}`}
                variants={msgVariants}
                initial="initial"
                animate="animate"
                exit="exit"
                transition={springTransition}
              >
                <div className={`msg-bubble ${isUser ? 'msg-user' : 'msg-ai'}`}>
                  <span className="msg-label">{isUser ? 'You' : 'MAX'}</span>
                  <p className="msg-text">{msg.content}</p>
                </div>
              </motion.article>
            )
          })}

          {/* Typing indicator */}
          {isProcessing && (
            <motion.article
              key="typing"
              className="msg-row msg-row-ai"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
            >
              <div className="msg-bubble msg-ai">
                <span className="msg-label">MAX</span>
                <div className="typing-dots" aria-label="MAX is thinking">
                  <i /><i /><i />
                </div>
              </div>
            </motion.article>
          )}
        </AnimatePresence>
      </div>
    </section>
  )
}
