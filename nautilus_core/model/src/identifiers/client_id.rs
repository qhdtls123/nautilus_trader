// -------------------------------------------------------------------------------------------------
//  Copyright (C) 2015-2023 Nautech Systems Pty Ltd. All rights reserved.
//  https://nautechsystems.io
//
//  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
//  You may not use this file except in compliance with the License.
//  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
//
//  Unless required by applicable law or agreed to in writing, software
//  distributed under the License is distributed on an "AS IS" BASIS,
//  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//  See the License for the specific language governing permissions and
//  limitations under the License.
// -------------------------------------------------------------------------------------------------

use std::{
    ffi::{c_char, CStr},
    fmt::{Debug, Display, Formatter},
    hash::Hash,
};

use nautilus_core::correctness;
use pyo3::prelude::*;
use ustr::Ustr;

#[repr(C)]
#[derive(Clone, Copy, Hash, PartialEq, Eq, PartialOrd, Ord)]
#[pyclass]
pub struct ClientId {
    pub value: Ustr,
}

impl ClientId {
    #[must_use]
    pub fn new(s: &str) -> Self {
        correctness::valid_string(s, "`ClientId` value");

        Self {
            value: Ustr::from(s),
        }
    }
}

impl Debug for ClientId {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        write!(f, "{:?}", self.value)
    }
}

impl Display for ClientId {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.value)
    }
}

////////////////////////////////////////////////////////////////////////////////
// C API
////////////////////////////////////////////////////////////////////////////////
/// Returns a Nautilus identifier from C string pointer.
///
/// # Safety
///
/// - Assumes `ptr` is a valid C string pointer.
#[no_mangle]
pub unsafe extern "C" fn client_id_new(ptr: *const c_char) -> ClientId {
    assert!(!ptr.is_null(), "`ptr` was NULL");
    ClientId::new(CStr::from_ptr(ptr).to_str().expect("CStr::from_ptr failed"))
}

#[no_mangle]
pub extern "C" fn client_id_hash(id: &ClientId) -> u64 {
    id.value.precomputed_hash()
}

////////////////////////////////////////////////////////////////////////////////
// Tests
////////////////////////////////////////////////////////////////////////////////
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_string_reprs() {
        let id = ClientId::new("BINANCE");
        assert_eq!(id.to_string(), "BINANCE");
        assert_eq!(format!("{id}"), "BINANCE");
    }

    #[test]
    fn test_client_id_to_cstr_c() {
        let id = ClientId::new("BINANCE");
        let c_string = id.value.as_char_ptr();
        let rust_string = unsafe { CStr::from_ptr(c_string) }.to_str().unwrap();
        assert_eq!(rust_string, "BINANCE");
    }

    #[test]
    fn test_client_id_hash_c() {
        let id1 = ClientId::new("BINANCE");
        let id2 = ClientId::new("BINANCE");
        let id3 = ClientId::new("DYDX");
        assert_eq!(client_id_hash(&id1), client_id_hash(&id2));
        assert_ne!(client_id_hash(&id1), client_id_hash(&id3));
    }
}
