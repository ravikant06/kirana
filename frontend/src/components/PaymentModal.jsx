import { useEffect, useRef, useState } from 'react'
import { api, money } from '../api.js'

// Shows the payment gateway's own checkout for one payment session.
//   mock:     payment-mock's page in a frame; it reports back with window.postMessage.
//   razorpay: Razorpay's checkout.js modal; it reports back through its handler.
// The browser only passes the signed result on. The backend's verify call decides.

const RAZORPAY_SCRIPT = 'https://checkout.razorpay.com/v1/checkout.js'

function loadScript(src) {
  if (document.querySelector(`script[src="${src}"]`) && window.Razorpay) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const el = document.createElement('script')
    el.src = src
    el.onload = () => resolve()
    el.onerror = () => reject(new Error('Could not load the payment window. Check your connection.'))
    document.body.appendChild(el)
  })
}

function MockCheckout({ session, onResult }) {
  useEffect(() => {
    const origin = new URL(session.checkoutUrl).origin
    const onMessage = (e) => {
      // Accept messages only from the gateway's page, and only about this payment.
      if (e.origin !== origin || e.data?.source !== 'payment-mock' || e.data.orderId !== session.gatewayOrderId) return
      if (e.data.status === 'paid') {
        onResult({
          status: 'paid',
          gatewayOrderId: e.data.razorpay_order_id,
          paymentId: e.data.razorpay_payment_id,
          signature: e.data.razorpay_signature,
        })
      } else {
        onResult({ status: e.data.status, reason: e.data.reason })
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [session, onResult])

  return (
    <div className="pay-overlay" role="dialog" aria-modal="true" aria-label="Payment">
      <div className="pay-frame-wrap">
        <iframe className="pay-frame" src={session.checkoutUrl} title="Kirana test gateway" />
        <button className="btn-quiet pay-close" onClick={() => onResult({ status: 'dismissed' })}>Close</button>
      </div>
    </div>
  )
}

function RazorpayCheckout({ session, onResult }) {
  const opened = useRef(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (opened.current) return
    opened.current = true
    loadScript(RAZORPAY_SCRIPT)
      .then(() => {
        const rzp = new window.Razorpay({
          key: session.keyId,
          order_id: session.gatewayOrderId,
          amount: session.amountPaise,
          currency: session.currency,
          name: 'Kirana',
          description: `Order #${session.orderId}`,
          handler: (r) =>
            onResult({
              status: 'paid',
              gatewayOrderId: r.razorpay_order_id,
              paymentId: r.razorpay_payment_id,
              signature: r.razorpay_signature,
            }),
          modal: { ondismiss: () => onResult({ status: 'dismissed' }) },
        })
        rzp.open()
      })
      .catch((e) => setError(e.message))
  }, [session, onResult])

  if (!error) return null
  return (
    <div className="pay-overlay" role="alertdialog" aria-modal="true">
      <div className="pay-frame-wrap pay-error">
        <p>{error}</p>
        <button className="btn" onClick={() => onResult({ status: 'dismissed' })}>Close</button>
      </div>
    </div>
  )
}

export default function PaymentModal({ session, onResult }) {
  if (session.provider === 'razorpay') return <RazorpayCheckout session={session} onResult={onResult} />
  return <MockCheckout session={session} onResult={onResult} />
}

// Runs one payment from session to settled order. Used by Cart (after placing) and Orders (Pay now).
export function usePayment({ notify, onDone }) {
  const [session, setSession] = useState(null)

  const handleResult = async (result) => {
    const s = session
    setSession(null)
    if (!s) return
    if (result.status === 'paid') {
      try {
        await api.orders.verify(s.orderId, {
          gatewayOrderId: result.gatewayOrderId,
          paymentId: result.paymentId,
          signature: result.signature,
        })
        notify(`Order #${s.orderId} paid: ${money(s.amountPaise / 100)}`)
      } catch (e) {
        notify(e.message, 'error')
      }
    } else if (result.status === 'failed') {
      notify(`Payment failed${result.reason ? `: ${result.reason}` : ''}. Try again or cancel from Orders.`, 'error')
    } else {
      notify(`Order #${s.orderId} is waiting for payment. Pay or cancel it from Orders.`)
    }
    onDone?.(s.orderId, result.status)
  }

  const modal = session ? <PaymentModal session={session} onResult={handleResult} /> : null
  return { start: setSession, modal }
}
