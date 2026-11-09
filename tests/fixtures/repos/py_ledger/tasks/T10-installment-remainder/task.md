Bug: installments loses cents. Splitting 100.00 into 3 instalments returns [33.33, 33.33, 33.33], which adds up to 99.99. The last instalment must absorb the remainder: [33.33, 33.33, 33.34].
